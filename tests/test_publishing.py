import unittest

import httpx

from app import publishing


class PublishingFilenameTests(unittest.TestCase):
    def test_content_disposition_is_ascii_with_utf8_filename(self):
        header = publishing._content_disposition_filename(
            'congo-santé-brazzaville-formation-clé',
            '.png',
        )

        header.encode('ascii')
        self.assertIn('filename="congo-sante-brazzaville-formation-cle.png"', header)
        self.assertIn("filename*=UTF-8''congo-sant%C3%A9-brazzaville-formation-cl%C3%A9.png", header)


class FailingAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        request = httpx.Request('POST', url)
        return httpx.Response(504, request=request)


class PublishingImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_routerai_image_returns_none_on_http_failure(self):
        original_client = publishing.httpx.AsyncClient
        publishing.httpx.AsyncClient = FailingAsyncClient
        try:
            image = await publishing.routerai_image('https://routerai.ru/api/v1', 'key', 'model', 'prompt')
        finally:
            publishing.httpx.AsyncClient = original_client

        self.assertIsNone(image)


class RetrospectiveSchedulingTests(unittest.TestCase):
    def test_retrospective_articles_for_same_day_share_publication_date(self):
        from app.worker import retrospective_scheduled_for

        scheduled_times = [retrospective_scheduled_for('2026-06-08') for _ in range(4)]

        self.assertEqual(len(set(scheduled_times)), 1)
        self.assertEqual(scheduled_times[0].isoformat(), '2026-06-08T00:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
