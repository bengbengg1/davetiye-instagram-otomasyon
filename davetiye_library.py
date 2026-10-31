"""Offline canonical library for Davetiye Instagram (49 headlines/3 rotating captions/14 songs)."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'content' if (ROOT / 'content' / 'basliklar_49.json').is_file() else ROOT
EXCLUDED_TEXT = 'Shrek severlerin hayalindeki düğün davetiyesi bulundu! 💚'

def load_library():
    headlines = json.loads((DATA / 'basliklar_49.json').read_text('utf-8'))
    captions = json.loads((DATA / 'aciklamalar_3.json').read_text('utf-8'))
    songs = json.loads((DATA / 'sarkilar_14.json').read_text('utf-8'))
    if (len(headlines),len(captions),len(songs)) != (49,3,14):
        raise ValueError('Expected 49 headlines, 3 captions, 14 songs')
    if len({i['id'] for i in headlines}) != 49 or len({i['overlay_text'] for i in headlines}) != 49:
        raise ValueError('Duplicate headline')
    if EXCLUDED_TEXT in [x['overlay_text'] for x in headlines]:
        raise ValueError('Excluded Shrek line cannot be included')
    if len({x['id'] for x in captions}) != 3 or len({x['id'] for x in songs}) != 14:
        raise ValueError('Duplicate caption or song ID')
    return headlines, captions, songs

def build_rotation():
    headlines, captions, songs = load_library()
    return [{
        'sequence': i+1,
        'overlay_id': h['id'], 'overlay_text': h['overlay_text'],
        'caption_id': captions[i % len(captions)]['id'],
        'caption': captions[i % len(captions)]['caption'],
        'song_id': songs[i % len(songs)]['id'],
        'song': f"{songs[i % len(songs)]['artist']} - {songs[i % len(songs)]['song']}",
        'music_status':'manual_selection_and_rights_review_required',
    } for i, h in enumerate(headlines)]

if __name__=='__main__':
    rows=build_rotation()
    print(json.dumps({'headlines':len(rows),'captions':3,'songs':14,'first':rows[0]['overlay_id'],'last':rows[-1]['overlay_id']},ensure_ascii=False,indent=2))
