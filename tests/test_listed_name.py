"""Folder name follows the name listed in qBittorrent."""

import unittest

from qbit_sorter.listed_name import plan_listed_rename


class ListedNamePlanTest(unittest.TestCase):
    def test_folder_renames_to_listed_name_when_they_differ(self):
        plan = plan_listed_rename(
            listed="Overthrow - Stephen Kinzer",
            content_path="/Audiobooks/Overthrow (Unabridged)",
            save_path="/Audiobooks",
            taken_names={"Overthrow (Unabridged)"},
        )

        self.assertFalse(plan.skipped)
        self.assertEqual(plan.old, "Overthrow (Unabridged)")
        self.assertEqual(plan.new, "Overthrow - Stephen Kinzer")

    def test_skips_when_folder_already_matches_listed_name(self):
        plan = plan_listed_rename(
            listed="Overthrow - Stephen Kinzer",
            content_path="/Audiobooks/Overthrow - Stephen Kinzer",
            save_path="/Audiobooks",
            taken_names={"Overthrow - Stephen Kinzer"},
        )
        self.assertTrue(plan.skipped)
        self.assertEqual(plan.note, "already matches")

    def test_skips_when_metadata_has_no_content_path(self):
        plan = plan_listed_rename(
            listed="John Adams - David McCullough",
            content_path="",
            save_path="/Audiobooks",
            taken_names=set(),
        )
        self.assertTrue(plan.skipped)
        self.assertEqual(plan.note, "no content path")

    def test_skips_slash_and_windows_illegal_listed_names(self):
        slash = plan_listed_rename(
            listed="Author/Title",
            content_path="/Audiobooks/Release Name",
            save_path="/Audiobooks",
            taken_names={"Release Name"},
        )
        illegal = plan_listed_rename(
            listed="Title: Sub",
            content_path="/Audiobooks/Release Name",
            save_path="/Audiobooks",
            taken_names={"Release Name"},
        )
        self.assertTrue(slash.skipped)
        self.assertEqual(slash.note, "listed name is not a folder name")
        self.assertTrue(illegal.skipped)
        self.assertEqual(illegal.note, "listed name is not a folder name")

    def test_skips_when_target_folder_already_exists(self):
        plan = plan_listed_rename(
            listed="Overthrow - Stephen Kinzer",
            content_path="/Audiobooks/Overthrow (Unabridged)",
            save_path="/Audiobooks",
            taken_names={"Overthrow (Unabridged)", "Overthrow - Stephen Kinzer"},
        )
        self.assertTrue(plan.skipped)
        self.assertEqual(plan.note, "target folder already exists")

    def test_nested_content_uses_path_relative_to_save_path(self):
        plan = plan_listed_rename(
            listed="Serve - C.W. Farnsworth",
            content_path="/Audiobooks/C.W. Farnsworth - Serve/disc1",
            save_path="/Audiobooks",
            taken_names={"C.W. Farnsworth - Serve"},
        )
        self.assertFalse(plan.skipped)
        self.assertEqual(plan.old, "C.W. Farnsworth - Serve/disc1")
        self.assertEqual(plan.new, "Serve - C.W. Farnsworth")

    def test_dry_run_does_not_call_rename_and_live_does(self):
        from types import SimpleNamespace
        from qbit_sorter.listed_name import sync

        torrents = [{
            "hash": "abc",
            "name": "Overthrow - Stephen Kinzer",
            "content_path": "/Audiobooks/Overthrow (Unabridged)",
            "save_path": "/Audiobooks",
            "category": "Audiobooks",
        }]

        class Fake:
            def __init__(self):
                self.calls = []
            def in_category(self, category):
                return torrents
            def rename_folder(self, torrent_hash, old, new):
                self.calls.append((torrent_hash, old, new))

        cfg = SimpleNamespace(
            dry_run=True,
            listed_name=SimpleNamespace(enabled=True, categories=["Audiobooks"]),
        )
        client = Fake()
        preview = sync(cfg, client)
        self.assertEqual(client.calls, [])
        self.assertFalse(preview[0]["skipped"])
        self.assertFalse(preview[0]["applied"])
        self.assertEqual(preview[0]["new"], "Overthrow - Stephen Kinzer")

        cfg.dry_run = False
        applied = sync(cfg, client)
        self.assertEqual(client.calls, [("abc", "Overthrow (Unabridged)", "Overthrow - Stephen Kinzer")])
        self.assertTrue(applied[0]["applied"])

    def test_apply_renames_while_global_dry_run_stays_on(self):
        from types import SimpleNamespace
        from qbit_sorter.listed_name import sync

        class Fake:
            def __init__(self):
                self.calls = []
            def in_category(self, category):
                return [{
                    "hash": "abc",
                    "name": "Overthrow - Stephen Kinzer",
                    "content_path": "/Audiobooks/Overthrow (Unabridged)",
                    "save_path": "/Audiobooks",
                }]
            def rename_folder(self, torrent_hash, old, new):
                self.calls.append((old, new))

        cfg = SimpleNamespace(
            dry_run=True,
            listed_name=SimpleNamespace(enabled=True, categories=["Audiobooks"], apply=True),
        )
        client = Fake()
        sync(cfg, client)
        self.assertEqual(client.calls, [("Overthrow (Unabridged)", "Overthrow - Stephen Kinzer")])


if __name__ == "__main__":
    unittest.main()
