import unittest
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from app import main
from app.models import PublishingSettings
from app.schemas import PublishingSettingsIn


class FakeSession:
    def __init__(self):
        self.statements = []
        self.committed = False

    def execute(self, statement):
        self.statements.append(statement)

    def commit(self):
        self.committed = True

    def refresh(self, _instance):
        pass


class PublishingSettingsUpdateTests(unittest.TestCase):
    def test_rewrite_model_change_updates_unfinished_jobs(self):
        db = FakeSession()
        settings = PublishingSettings(
            id=1,
            routerai_base_url='https://routerai.ru/api/v1',
            rewrite_model='deepseek-pro',
            default_language='en',
        )

        with patch.object(main, 'get_or_create_settings', return_value=settings):
            main.update_publishing_settings(
                PublishingSettingsIn(rewrite_model='openai/gpt-4o-mini'),
                db=db,
            )

        self.assertEqual(settings.rewrite_model, 'openai/gpt-4o-mini')
        self.assertTrue(db.committed)
        self.assertEqual(len(db.statements), 1)
        statement = str(db.statements[0].compile(dialect=postgresql.dialect()))
        self.assertIn('UPDATE publish_jobs SET rewrite_model=', statement)
        self.assertIn('publish_jobs.status NOT IN', statement)

    def test_other_settings_do_not_change_job_model(self):
        db = FakeSession()
        settings = PublishingSettings(
            id=1,
            routerai_base_url='https://routerai.ru/api/v1',
            rewrite_model='deepseek-pro',
            default_language='en',
        )

        with patch.object(main, 'get_or_create_settings', return_value=settings):
            main.update_publishing_settings(PublishingSettingsIn(default_language='fr'), db=db)

        self.assertEqual(settings.rewrite_model, 'deepseek-pro')
        self.assertEqual(db.statements, [])
        self.assertTrue(db.committed)


if __name__ == '__main__':
    unittest.main()
