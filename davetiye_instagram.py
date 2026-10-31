"""Davetiye Instagram Reels studio — one distinct owner-supplied video per day.

Uses the existing Piyaso repo's storage_client.client(), boto3, FFmpeg, Pillow.
Only keys under davetiye-instagram/ are accessed, and only inbox videos and
consumed plan records are deleted. Instagram/Buffer publishing is NOT implemented.

A song NAME is only a reminder for Buffer's web music picker. No music audio
is added and no copyrighted audio is fetched. Instagram upload is disabled.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

PREFIX = 'davetiye-instagram/'
INBOX = PREFIX + 'inbox/'
PLAN = PREFIX + 'plan/pending/'
DAY = PREFIX + 'state/days/'
USED_MEDIA = PREFIX + 'state/used-media/'
USED_PLAN = PREFIX + 'state/used-plan/'
DRAFTS = PREFIX + 'drafts/'
VALID_EXTS = {'.mp4', '.mov', '.mkv'}
CONTENT_TYPES = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska'}
MAX_SOURCE_BYTES = 250 * 1024 * 1024
MAX_PLAN_BYTES = 60_000
WIDTH, HEIGHT, FPS = 720, 1280, 30
DEFAULT_SECONDS = 18


def canon(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    return ' '.join(value.split())


def encode(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def check_key(key, expected_prefix=PREFIX):
    if not (isinstance(key, str) and key.startswith(expected_prefix)
            and '..' not in key.split('/') and '\\' not in key and '\n' not in key):
        raise ValueError('Davetiye alanı dışında veya güvensiz R2 anahtarı: ' + str(key))
    return key


def exists_error(exc):
    code = str(getattr(exc, 'response', {}).get('Error', {}).get('Code', ''))
    return code in ('PreconditionFailed', '412', 'ConditionalRequestConflict', '409')


def missing_error(exc):
    code = str(getattr(exc, 'response', {}).get('Error', {}).get('Code', ''))
    return code in ('404', 'NoSuchKey', 'NotFound')


class Store:
    def __init__(self, s3=None):
        # Reuse Piyaso storage_client when present; otherwise run in a separate GitHub repository.
        try:
            import storage_client
        except ImportError:
            storage_client = None
        self.bucket = 'piyaso-tiktok-assets'
        if storage_client is not None:
            self.s3 = s3 if s3 is not None else storage_client.client()
            if storage_client.bucket_name('assets') != self.bucket:
                raise RuntimeError('Beklenmeyen R2 bucket; başka depolama alanına yazılmaz')
        else:
            if os.environ.get('R2_ASSETS_BUCKET', self.bucket) != self.bucket:
                raise RuntimeError('Beklenmeyen R2 bucket; başka depolama alanına yazılmaz')
            if s3 is not None:
                self.s3 = s3
            else:
                import boto3
                required = ('R2_ENDPOINT','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY')
                missing = [k for k in required if not os.environ.get(k)]
                if missing:
                    raise RuntimeError('Eksik R2 GitHub secrets: '+', '.join(missing))
                self.s3 = boto3.client('s3',region_name='auto',endpoint_url=os.environ['R2_ENDPOINT'],
                    aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                    aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'])

    def keys(self, prefix):
        check_key(prefix)
        records = []
        for page in self.s3.get_paginator('list_objects_v2').paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get('Contents', []):
                key = item['Key']
                if not key.startswith(prefix) or key.endswith('/'):
                    continue
                records.append(item)
        return sorted(records, key=lambda x: (x.get('LastModified', dt.datetime.min.replace(tzinfo=dt.timezone.utc)), x['Key']))

    def read(self, key, *, limit=MAX_PLAN_BYTES):
        check_key(key)
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if missing_error(exc):
                return None
            raise
        try:
            if response.get('ContentLength', 0) > limit:
                raise ValueError('R2 JSON boyutu çok büyük: ' + key)
            raw = response['Body'].read(limit + 1)
            if len(raw) > limit:
                raise ValueError('R2 JSON boyutu çok büyük: ' + key)
            return json.loads(raw)
        finally:
            response['Body'].close()

    def create(self, key, data):
        check_key(key)
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=encode(data), ContentType='application/json', IfNoneMatch='*')
            return True
        except Exception as exc:
            if exists_error(exc):
                if self.read(key) != data:
                    raise RuntimeError('Önceden yazılmış kayıt farklı; üzerine yazılmadı: ' + key)
                return False
            raise

    def put_bytes_once(self, key, data, mime):
        check_key(key)
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=mime, IfNoneMatch='*')
            return True
        except Exception as exc:
            if exists_error(exc):
                meta = self.s3.head_object(Bucket=self.bucket, Key=key)
                if meta.get('ContentLength') != len(data):
                    raise RuntimeError('Hedef dosya zaten mevcut ve boyutu farklı: ' + key)
                return False
            raise

    def verified(self, key, size):
        check_key(key)
        try:
            meta = self.s3.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if missing_error(exc):
                return False
            raise
        return meta.get('ContentLength') == size

    def download(self, key, destination):
        check_key(key, INBOX)
        self.s3.download_file(self.bucket, key, str(destination))

    def delete_consumed(self, key):
        # Deletion never touches Piyaso, other Instagram state, or output drafts.
        if not (key.startswith(INBOX) and Path(key).suffix.lower() in VALID_EXTS
                or key.startswith(PLAN) and key.endswith('.json')):
            raise ValueError('Bu nesnenin otomatik silinmesine izin verilmedi: ' + key)
        check_key(key)
        self.s3.delete_object(Bucket=self.bucket, Key=key)


def plan_entry(overlay_text, caption, song):
    if not all(isinstance(x, str) for x in (overlay_text, caption, song)):
        raise ValueError('overlay_text, caption ve song metin olmalı')
    overlay_text, caption, song = (x.strip() for x in (overlay_text, caption, song))
    if not (1 <= len(overlay_text) <= 300):
        raise ValueError('Video üstü yazı 1–300 karakter olmalı')
    if not (1 <= len(caption) <= 2200):
        raise ValueError('Instagram açıklaması 1–2200 karakter olmalı')
    if not (1 <= len(song) <= 180):
        raise ValueError('Şarkı adı/sanatçı 1–180 karakter olmalı')
    key = canon('\u241e'.join([overlay_text, caption, song]))
    id_ = hashlib.sha256(key.encode()).hexdigest()[:32]
    return {'schema': 1, 'id': id_, 'overlay_text': overlay_text, 'caption': caption, 'song': song,
            'music_status': 'buffer_manual_selection_required'}


def parse_plans(content):
    content = content.strip().lstrip('\ufeff')
    if not content:
        raise ValueError('İçerik listesi boş')
    if content.startswith(('[', '{')):
        source = json.loads(content)
        if isinstance(source, dict):
            texts = source.get('overlay_texts')
            captions = source.get('captions')
            songs = source.get('songs')
            if (not all(isinstance(x, list) for x in (texts, captions, songs))
                    or len(texts) != len(captions) or len(texts) != len(songs)):
                raise ValueError('Ayrı metin, açıklama ve şarkı listelerinin uzunluğu eşit olmalı')
            rows = list(zip(texts, captions, songs))
        elif isinstance(source, list):
            rows = [(row['overlay_text'], row['caption'], row['song']) for row in source]
        else:
            raise ValueError('JSON liste veya overlay_texts/captions/songs nesnesi olmalı')
    else:
        rows = []
        for line in content.splitlines():
            if line.strip():
                parts = [x.strip() for x in line.split(' || ')]
                if len(parts) != 3:
                    raise ValueError('Her satır: EKRAN YAZISI || AÇIKLAMA || ŞARKI şeklinde olmalı')
                rows.append(parts)
    if not 1 <= len(rows) <= 100:
        raise ValueError('Tek seferde 1–100 günlük içerik girilebilir')
    result = [plan_entry(*r) for r in rows]
    if len({r['id'] for r in result}) != len(result):
        raise ValueError('Listede aynı içerik tekrar ediyor')
    return result


def import_plans(store, content):
    rows = parse_plans(content)
    counts = {'added': 0, 'already_present': 0, 'previously_used': 0}
    for item in rows:
        id_ = item['id']
        if store.read(USED_PLAN + id_ + '.json') is not None:
            counts['previously_used'] += 1
            continue
        key = PLAN + id_ + '.json'
        existing = store.read(key)
        if existing is not None:
            if any(existing.get(k) != item[k] for k in ('id', 'overlay_text', 'caption', 'song')):
                raise RuntimeError('Aynı kimlik farklı içerik taşıyor; kayıt değiştirilmedi')
            counts['already_present'] += 1
            continue
        item = {**item, 'added_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        if store.create(key, item):
            counts['added'] += 1
        else:
            counts['already_present'] += 1
    return counts




def seed_library(store):
    """Idempotently insert exactly 49 immutable combinations from committed local files.

    Music is a NAME only, not embedded/licensed/downloaded. Captions rotate 1-2-3;
    titles are never reused, even when source videos are exhausted.
    """
    from davetiye_library import build_rotation
    rows = build_rotation()
    result = {'total':len(rows),'added':0,'already_present':0,'previously_used':0}
    for row in rows:
        validated = plan_entry(row['overlay_text'],row['caption'],row['song'])
        # Stable queue identity keeps ordering independent of R2 LastModified timestamps.
        plan_id = f"{row['sequence']:04d}-{validated['id']}"
        key = PLAN + plan_id + '.json'
        if store.read(USED_PLAN + plan_id + '.json') is not None:
            result['previously_used'] += 1
            continue
        plan = {**validated, 'id':plan_id, 'sequence':row['sequence'],
                'overlay_id':row['overlay_id'], 'caption_id':row['caption_id'],
                'song_id':row['song_id'], 'added_at':'seeded_from_git'}
        existing = store.read(key)
        if existing is not None:
            if existing != plan:
                raise RuntimeError('Bekleyen R2 kaydı farklı, üzerine yazılmadı: '+key)
            result['already_present'] += 1
            continue
        if store.create(key,plan):
            result['added'] += 1
        else:
            result['already_present'] += 1
    return result

def inventory(store):
    days = [row for row in store.keys(DAY) if row['Key'].endswith('/selection.json')]
    assigned_sources, assigned_plans = set(), set()
    for row in days:
        doc = store.read(row['Key'])
        assigned_sources.add(doc['source_key'])
        assigned_plans.add(doc['plan_id'])
    media = [x for x in store.keys(INBOX) if Path(x['Key']).suffix.lower() in VALID_EXTS and '/' not in x['Key'][len(INBOX):]]
    plans = [x for x in store.keys(PLAN) if x['Key'].endswith('.json')]
    return {'raw_videos_available': sum(x['Key'] not in assigned_sources for x in media),
            'text_song_sets_available': sum(x['Key'][len(PLAN):-5] not in assigned_plans for x in plans),
            'video_drafts_created': sum(x['Key'].endswith('/metadata.json') for x in store.keys(DRAFTS)),
            'used_raw_archives': len(store.keys(USED_MEDIA)),
            'music_selection': 'manual_in_Buffer', 'instagram_publishing': 'disabled'}


def reserve(store, day):
    dt.date.fromisoformat(day)
    key = DAY + day + '/selection.json'
    saved = store.read(key)
    if saved is not None:
        return saved
    old = [store.read(r['Key']) for r in store.keys(DAY) if r['Key'].endswith('/selection.json')]
    taken_sources = {s['source_key'] for s in old}
    taken_plan_ids = {s['plan_id'] for s in old}
    source = next((r for r in store.keys(INBOX)
                   if r['Key'] not in taken_sources
                   and Path(r['Key']).suffix.lower() in VALID_EXTS
                   and 0 < int(r.get('Size', 0)) <= MAX_SOURCE_BYTES
                   and '/' not in r['Key'][len(INBOX):]), None)
    if source is None:
        raise RuntimeError('Yeni davetiye ana videosu yok. Özel R2 inbox klasörüne video yükleyin.')
    # Numeric queue IDs from the bundled library are processed in order.
    # Legacy plans remain supported; reserved plans are never reselected.
    plan = next((r for r in sorted(store.keys(PLAN), key=lambda r: r['Key'])
                 if r['Key'].endswith('.json')
                 and r['Key'][len(PLAN):-5] not in taken_plan_ids), None)
    if plan is None:
        raise RuntimeError('Yeni ekran yazısı + açıklama + şarkı listesi yok. Önce plan ekleyin.')
    doc = {'date': day, 'source_key': source['Key'], 'source_bytes': source['Size'],
           'source_etag': source.get('ETag', ''), 'plan_id': plan['Key'][len(PLAN):-5], 'plan_key': plan['Key']}
    store.create(key, doc)
    return doc


def fonts():
    return [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
    ]


def overlay_image(text, dest):
    from PIL import Image, ImageDraw, ImageFont
    canvas = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    painter = ImageDraw.Draw(canvas)
    fontpath = next((p for p in fonts() if Path(p).is_file()), None)
    if not fontpath:
        raise RuntimeError('Kalın yazı tipi bulunamadı')
    boxw, boxh = 632, 450
    for size in range(58, 21, -2):
        f = ImageFont.truetype(fontpath, size)
        lines, current = [], ''
        for word in text.split():
            attempt = (current + ' ' + word).strip()
            if painter.textbbox((0, 0), attempt, font=f, stroke_width=3)[2] <= boxw:
                current = attempt
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        height = int(size * 1.35)
        if (len(lines) <= 8 and len(lines) * height <= boxh and
                all(painter.textbbox((0, 0), l, font=f, stroke_width=3)[2] <= boxw for l in lines)):
            top = 160 + (boxh - len(lines) * height) // 2
            for line in lines:
                bounds = painter.textbbox((0, 0), line, font=f, stroke_width=3)
                xx = (WIDTH - (bounds[2] - bounds[0])) // 2
                painter.text((xx, top), line, font=f, fill='white', stroke_width=3, stroke_fill='black')
                top += height
            canvas.save(dest)
            return
    raise ValueError('Video üstü yazı 720p alana sığmıyor; yazıyı kısaltın')


def video_duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                       text=True, capture_output=True, check=True, timeout=30)
    return float(r.stdout.strip())


def render_video(raw, overlay, out):
    seconds = video_duration(raw)
    if not 5 <= seconds <= 60 * 60:
        raise ValueError('Video 5 saniyeden kısa veya 60 dakikadan uzun')
    target = min(DEFAULT_SECONDS, seconds)
    vf = (f'[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,'
          f'crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},format=yuv420p[base];'
          '[base][1:v]overlay=0:0:format=auto,format=yuv420p[v]')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                    '-i', str(raw), '-loop', '1', '-i', str(overlay),
                    '-filter_complex', vf, '-map', '[v]', '-t', str(target),
                    '-an', '-c:v', 'libx264', '-crf', '23', '-preset', 'fast',
                    '-movflags', '+faststart', str(out)],
                   check=True, timeout=1200)
    final = video_duration(out)
    if final < target - .4:
        raise RuntimeError('Instagram Reels videosu eksik oluştu')
    if not (0 < out.stat().st_size <= 300_000_000):
        raise ValueError('Instagram video boyutu geçersiz')
    return round(final, 2)


def complete(store, day, selection, plan, *, output, size, checksum, duration, source_sha):
    base = DRAFTS + day + '/'
    receipt = {'date': day, 'status': 'ready_for_music_review',
               'source_key': selection['source_key'], 'source_sha256': source_sha,
               'plan_id': selection['plan_id'], 'overlay_text': plan['overlay_text'],
               'caption': plan['caption'], 'song': plan['song'],
               'song_audio_embedded': False, 'music_status': 'needs_manual_Buffer_song_selection',
               'music_rights_require_review': True, 'instagram_posted': False,
               'buffer_posted': False, 'video_sha256': checksum, 'video_bytes': size,
               'duration_seconds': duration, 'resolution': '720x1280'}
    if not store.verified(output, size):
        raise RuntimeError('Özel R2 videoya kaydedilmedi; hiçbir ham dosya silinmedi')
    store.create(base + 'metadata.json', receipt)
    store.create(USED_MEDIA + source_sha + '.json', {
        'date': day, 'source_key': selection['source_key'], 'source_sha256': source_sha,
        'draft_key': output})
    store.create(USED_PLAN + selection['plan_id'] + '.json', {
        'date': day, 'plan_id': selection['plan_id'], 'draft_key': output})
    store.create(DAY + day + '/result.json', receipt)
    # Delete ONLY after all outputs and irreversible receipts exist.
    # On retry, completed receipts trigger this safe cleanup again if needed.
    store.delete_consumed(selection['source_key'])
    store.delete_consumed(selection['plan_key'])
    return receipt


def run_day(store, day):
    dt.date.fromisoformat(day)
    receipt = store.read(DAY + day + '/result.json')
    if receipt is not None:
        if not store.verified(DRAFTS + day + '/video.mp4', receipt['video_bytes']):
            raise RuntimeError('Günlük sonuç var fakat hazır video kayıp; silme engellendi')
        selection = store.read(DAY + day + '/selection.json')
        if selection:
            store.delete_consumed(selection['source_key'])
            store.delete_consumed(selection['plan_key'])
        return receipt
    selection = reserve(store, day)
    plan = store.read(selection['plan_key'])
    if plan is None:
        raise RuntimeError('Rezerve edilmiş açıklama kayboldu; ham videoyu silmiyoruz')
    if plan['id'] != selection['plan_id']:
        raise RuntimeError('Plan kimliği eşleşmiyor')
    with tempfile.TemporaryDirectory(prefix='davetiye-ig-') as dirname:
        temp = Path(dirname)
        raw = temp / ('source' + Path(selection['source_key']).suffix.lower())
        png = temp / 'text.png'
        mp4 = temp / 'reel.mp4'
        store.download(selection['source_key'], raw)
        meta = store.s3.head_object(Bucket=store.bucket, Key=selection['source_key'])
        if (raw.stat().st_size != selection['source_bytes'] or
                (selection.get('source_etag') and meta.get('ETag') and
                 str(meta['ETag']) != str(selection['source_etag']))):
            raise RuntimeError('Kaynak video değişmiş; güvenli durduruldu')
        source_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        if store.read(USED_MEDIA + source_sha + '.json') is not None:
            raise RuntimeError('Bu ana video daha önce başka bir Reels için kullanılmış')
        overlay_image(plan['overlay_text'], png)
        duration = render_video(raw, png, mp4)
        media = mp4.read_bytes()
        output = DRAFTS + day + '/video.mp4'
        store.put_bytes_once(output, media, 'video/mp4')
        # Private individual files are used by the future Buffer integration.
        store.put_bytes_once(DRAFTS + day + '/caption.txt', plan['caption'].encode('utf-8'), 'text/plain; charset=utf-8')
        store.create(DRAFTS + day + '/music.json', {'song': plan['song'], 'select_in': 'Buffer Instagram Reels composer',
                                                   'audio_embedded': False, 'selected': False})
        return complete(store, day, selection, plan, output=output, size=len(media),
                        checksum=hashlib.sha256(media).hexdigest(), duration=duration, source_sha=source_sha)


def main():
    parser = argparse.ArgumentParser(description='Davetiye Instagram: günlük 1 özel Reels taslağı')
    modes = parser.add_subparsers(dest='action', required=True)
    modes.add_parser('status')
    modes.add_parser('seed')
    add = modes.add_parser('add')
    add.add_argument('--file', type=Path)
    add.add_argument('--stdin', action='store_true')
    run = modes.add_parser('run')
    run.add_argument('--date', default=dt.datetime.now(ZoneInfo('Europe/Istanbul')).date().isoformat())
    run.add_argument('--skip-if-empty', action='store_true', help='Plan/video yoksa planlı çalışmada hata yerine atla')
    args = parser.parse_args()
    store = Store()
    if args.action == 'status':
        print(json.dumps(inventory(store), ensure_ascii=False, indent=2))
    elif args.action == 'seed':
        print(json.dumps(seed_library(store), ensure_ascii=False, indent=2))
    elif args.action == 'add':
        import sys
        if bool(args.file) == bool(args.stdin):
            parser.error('Tam bir kaynak belirtin: --file veya --stdin')
        raw = sys.stdin.read() if args.stdin else args.file.read_text('utf-8')
        print(json.dumps(import_plans(store, raw), ensure_ascii=False, indent=2))
    elif args.action == 'run':
        if args.skip_if_empty and store.read(DAY + args.date + '/selection.json') is None:
            current = inventory(store)
            if not current['raw_videos_available'] or not current['text_song_sets_available']:
                print(json.dumps({'date': args.date, 'status': 'skipped_no_ready_source_or_plan',
                                  **current}, ensure_ascii=False, indent=2))
                return
        print(json.dumps(run_day(store, args.date), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
