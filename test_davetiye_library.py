"""Offline tests for committed 49/3/14 rotation and idempotent R2 seeding."""
import unittest
import davetiye_instagram as di
from davetiye_library import build_rotation, load_library, EXCLUDED_TEXT
from test_davetiye_instagram import DavetiyeTests, MemoryS3

class ContentTests(DavetiyeTests):
    # Reuse memory S3 setup, separate test class to isolate seeding conditions.
    def test_immutable_exact_content_counts(self):
        headlines,captions,songs=load_library()
        self.assertEqual((len(headlines),len(captions),len(songs)),(49,3,14))
        self.assertNotIn(EXCLUDED_TEXT,[x['overlay_text'] for x in headlines])
        self.assertEqual([x['id'] for x in captions], ['CAPTION_001','CAPTION_002','CAPTION_003'])

    def test_three_caption_and_fourteen_song_rotation(self):
        rows=build_rotation()
        self.assertEqual([x['caption_id'] for x in rows[:7]],
                         ['CAPTION_001','CAPTION_002','CAPTION_003','CAPTION_001','CAPTION_002','CAPTION_003','CAPTION_001'])
        self.assertEqual(rows[0]['song_id'],rows[14]['song_id'])
        self.assertNotEqual(rows[0]['overlay_text'],rows[1]['overlay_text'])

    def test_seed_only_once(self):
        self.assertEqual(di.seed_library(self.store)['added'],49)
        again=di.seed_library(self.store)
        self.assertEqual(again['added'],0)
        self.assertEqual(again['already_present'],49)
        self.assertIn('state/copy/2026-10-30.json',self.s3.objects)

    def test_seed_already_used_remains_used(self):
        di.seed_library(self.store)
        plan_key=di.PLAN+sorted(x[len(di.PLAN):] for x in self.s3.objects if x.startswith(di.PLAN) and x.endswith('.json'))[0]
        plan_id=plan_key[len(di.PLAN):-5]
        self.store.create(di.USED_PLAN+plan_id+'.json',{'id':plan_id})
        self.assertEqual(di.seed_library(self.store)['previously_used'],1)
        # Seeding does not recreate used content under a different plan ID.
        self.assertEqual(len([x for x in self.s3.objects if x.startswith(di.PLAN) and x.endswith('.json')]),49)

    def test_video_consumes_distinct_prepared_texts_in_order(self):
        di.seed_library(self.store)
        for i in range(2):
            self.s3.objects[di.INBOX+f'clip{i}.mp4']=b'placeholder'+bytes([i])
        a=di.reserve(self.store,'2026-11-01')
        b=di.reserve(self.store,'2026-11-02')
        self.assertEqual([a['plan_id'][:4],b['plan_id'][:4]],['0001','0002'])
        self.assertNotEqual(a['source_key'],b['source_key'])
        self.assertEqual(self.store.read(a['plan_key'])['caption_id'],'CAPTION_001')
        self.assertEqual(self.store.read(b['plan_key'])['caption_id'],'CAPTION_002')

    def test_song_is_name_not_audio_and_publishing_disabled(self):
        for row in build_rotation():
            self.assertIn(' - ',row['song'])
            self.assertEqual(row['music_status'],'manual_selection_and_rights_review_required')

if __name__=='__main__': unittest.main()
