import os
import unittest

from worker import JobWorker
from app.storage import Storage


class JobWorkerTests(unittest.TestCase):
    def test_stage_mapping_covers_pipeline_milestones(self):
        mapping = JobWorker.STAGE_TO_STATUS
        self.assertEqual(mapping["extract"], "EXTRACTING")
        self.assertEqual(mapping["chunk"], "CHUNKING")
        self.assertEqual(mapping["tts"], "GENERATING_AUDIO")
        self.assertEqual(mapping["merge"], "MERGING")

    def test_storage_publish_returns_absolute_path(self):
        path = Storage.publish("/tmp/final.mp3", "job-id")
        self.assertEqual(path, "/tmp/final.mp3")
        rel = Storage.publish("output/final.mp3", "job-id")
        self.assertTrue(os.path.isabs(rel))
        self.assertTrue(rel.endswith(os.path.join("output", "final.mp3")))


if __name__ == "__main__":
    unittest.main()
