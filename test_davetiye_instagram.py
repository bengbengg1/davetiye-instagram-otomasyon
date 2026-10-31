"""Offline tests for isolated daily davetiye Instagram draft automation."""
from __future__ import annotations
import datetime as dt
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
if not (ROOT / 'davetiye_instagram.py').exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

import davetiye_instagram as di


class S3Exception(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}
        super().__init__(code)


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def put_object(self, Bucket, Key, Body, ContentType=None, IfNoneMatch=None):
        if Key in self.objects and IfNoneMatch == '*':
            raise S3Exception('PreconditionFailed')
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise S3Exception('NoSuchKey')
        raw = self.objects[Key]
        return {'ContentLength': len(raw), 'Body': io.BytesIO(raw)}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise S3Exception('404')
        return {'ContentLength': len(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)

    def download_file(self, Bucket, Key, Filename):
        Path(Filename).write_bytes(self.objects[Key])

    def get_paginator(self, name):
        s3 = self
        class Paginator:
            def paginate(self, Bucket, Prefix):
                rows = [dict(Key=key, Size=len(raw),
                             ETag='etag-' + str(len(raw)),
                             LastModified=dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc))
                        for key, raw in s3.objects.items() if key.startswith(Prefix)]
                return [{'Contents': rows}]
        return Paginator()


class DavetiyeTests(unittest.TestCase):
    def setUp(self):
        self.s3 = MemoryS3()
        client = types.SimpleNamespace(client=lambda: self.s3, bucket_name=lambda _: 'piyaso-tiktok-assets')
        self.patcher = patch.dict(sys.modules, {'storage_client': client})
        self.patcher.start()
        self.store = di.Store(self.s3)
        self.s3.objects['state/copy/2026-10-30.json'] = b'{"piyaso":true}'

    def tearDown(self):
        self.patcher.stop()

    def test_batch_three_parts_and_unique(self):
        text = 'Gelinlik hazır 😍 || Düğün davetiyemize bakın || Evgeny Grinko - Valse\n' \
               'Özel günümüz || Sonsuza dek || Ludovico Einaudi - Experience'
        items = di.parse_plans(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['song'], 'Evgeny Grinko - Valse')
        self.assertEqual(di.import_plans(self.store, text)['added'], 2)
        self.assertEqual(di.import_plans(self.store, text)['already_present'], 2)

    def test_json_batch_format(self):
        raw = json.dumps([{'overlay_text': 'Aşkımıza hoş geldiniz',
                           'caption': 'Davetiye tasarımı', 'song': 'Sanatçı - Şarkı'}], ensure_ascii=False)
        self.assertEqual(len(di.parse_plans(raw)), 1)

    def test_three_separate_lists_in_one_json(self):
        raw = json.dumps({'overlay_texts':['Ekran 1', 'Ekran 2'],
                          'captions':['Açıklama 1', 'Açıklama 2'],
                          'songs':['Sanatçı 1 - Şarkı 1', 'Sanatçı 2 - Şarkı 2']}, ensure_ascii=False)
        self.assertEqual([x['caption'] for x in di.parse_plans(raw)], ['Açıklama 1','Açıklama 2'])
        with self.assertRaisesRegex(ValueError, 'eşit olmalı'):
            di.parse_plans(json.dumps({'overlay_texts':['a'], 'captions':['b'], 'songs':[]}))

    def test_reject_duplicate_input(self):
        with self.assertRaisesRegex(ValueError, 'tekrar'):
            di.parse_plans('Aşkımız için || Bir açıklama || Bir şarkı\nAşkımız için || Bir açıklama || Bir şarkı')

    def test_source_allocation_and_retries(self):
        di.import_plans(self.store, 'Reels 1 || Caption 1 || Song 1\nReels 2 || Caption 2 || Song 2')
        self.s3.objects[di.INBOX + 'v1.mp4'] = b'fake-video-1'
        self.s3.objects[di.INBOX + 'v2.mp4'] = b'fake-video-2'
        first = di.reserve(self.store, '2026-11-01')
        self.assertEqual(di.reserve(self.store, '2026-11-01'), first)
        second = di.reserve(self.store, '2026-11-02')
        self.assertNotEqual(first['source_key'], second['source_key'])
        self.assertNotEqual(first['plan_id'], second['plan_id'])
        self.assertEqual(di.inventory(self.store)['raw_videos_available'], 0)

    def test_safe_deletion_scope(self):
        for bad in ['backgrounds/piyaso.mp4', di.DRAFTS + '2026-11-01/video.mp4',
                    di.PREFIX + 'state/used-media/abc.json', 'other/inbox/a.mp4']:
            with self.assertRaises(ValueError):
                self.store.delete_consumed(bad)
        self.assertEqual(self.s3.deleted, [])

    def test_no_media_no_plan_no_deletion(self):
        with self.assertRaisesRegex(RuntimeError, 'ana videosu'):
            di.run_day(self.store, '2026-11-01')
        self.s3.objects[di.INBOX + 'test.mp4'] = b'fake'
        with self.assertRaisesRegex(RuntimeError, 'listesi yok'):
            di.run_day(self.store, '2026-11-01')
        self.assertNotIn(di.INBOX + 'test.mp4', self.s3.deleted)

    def test_immutable_history_disallows_reimport(self):
        raw = 'Sevdiğime davetiye || El ele || İsim - Parça'
        item = di.parse_plans(raw)[0]
        self.store.create(di.USED_PLAN + item['id'] + '.json', {'id': item['id']})
        self.assertEqual(di.import_plans(self.store, raw)['previously_used'], 1)

    def test_complete_requires_video_before_deletion(self):
        source_key, plan_key = di.INBOX + 'test.mp4', di.PLAN + 'id.json'
        self.s3.objects[source_key] = b'raw'
        self.store.create(plan_key, {'id':'id'})
        selection = dict(source_key=source_key, plan_key=plan_key, plan_id='id')
        plan = dict(overlay_text='Düğün',caption='Bizim hikâyemiz',song='Artist - Music')
        with self.assertRaisesRegex(RuntimeError, 'kaydedilmedi'):
            di.complete(self.store, '2026-11-01', selection, plan, output=di.DRAFTS+'2026-11-01/video.mp4',
                        size=123, checksum='checksum',duration=18,source_sha='sha')
        self.assertIn(source_key, self.s3.objects)
        self.assertIn(plan_key, self.s3.objects)
        self.assertIn('state/copy/2026-10-30.json', self.s3.objects)

    def test_completed_run_cleanup_is_scoped_and_idempotent(self):
        d = '2026-11-01'
        video = di.DRAFTS + d + '/video.mp4'
        self.s3.objects[video] = b'finalvid'
        self.s3.objects[di.INBOX + 'our.mp4'] = b'raw'
        self.store.create(di.PLAN + 'id.json', dict(id='id'))
        selection = dict(source_key=di.INBOX+'our.mp4', plan_key=di.PLAN+'id.json', plan_id='id')
        self.store.create(di.DAY + d + '/selection.json', selection)
        r = di.complete(self.store, d, selection,
                        dict(overlay_text='İlk gün', caption='Davetiye', song='Track'),
                        output=video, size=8, checksum='sum', duration=18, source_sha='123abc')
        self.assertFalse(r['instagram_posted'])
        self.assertFalse(r['song_audio_embedded'])
        self.assertNotIn(selection['source_key'], self.s3.objects)
        self.assertNotIn(selection['plan_key'], self.s3.objects)
        self.assertEqual(di.run_day(self.store,d), r)
        self.assertIn('state/copy/2026-10-30.json', self.s3.objects)
        self.assertIn(video, self.s3.objects)


if __name__ == '__main__':
    unittest.main()
