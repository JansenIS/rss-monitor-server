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
        self.timeout = kwargs.get('timeout')


    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        request = httpx.Request('POST', url)
        return httpx.Response(504, request=request)


class ImageUrlAsyncClient:
    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get('timeout')


    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        request = httpx.Request('POST', url)
        return httpx.Response(200, request=request, json={'data': [{'image_url': 'https://cdn.example/image.png'}]})

    async def get(self, url, **kwargs):
        request = httpx.Request('GET', url)
        return httpx.Response(200, request=request, content=b'image-bytes')


class PublishingImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_routerai_image_returns_none_on_http_failure(self):
        original_client = publishing.httpx.AsyncClient
        publishing.httpx.AsyncClient = FailingAsyncClient
        try:
            image = await publishing.routerai_image('https://routerai.ru/api/v1', 'key', 'model', 'prompt')
        finally:
            publishing.httpx.AsyncClient = original_client

        self.assertIsNone(image)

    async def test_routerai_image_can_raise_on_http_failure(self):
        original_client = publishing.httpx.AsyncClient
        publishing.httpx.AsyncClient = FailingAsyncClient
        try:
            with self.assertRaises(publishing.RouterAIImageError):
                await publishing.routerai_image('https://routerai.ru/api/v1', 'key', 'model', 'prompt', raise_on_error=True)
        finally:
            publishing.httpx.AsyncClient = original_client

    async def test_routerai_image_accepts_image_url_field(self):
        original_client = publishing.httpx.AsyncClient
        publishing.httpx.AsyncClient = ImageUrlAsyncClient
        try:
            image = await publishing.routerai_image('https://routerai.ru/api/v1', 'key', 'model', 'prompt')
        finally:
            publishing.httpx.AsyncClient = original_client

        self.assertEqual(image, b'image-bytes')

    async def test_routerai_image_uses_long_generation_timeout(self):
        original_client = publishing.httpx.AsyncClient
        clients = []

        class CapturingAsyncClient(ImageUrlAsyncClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                clients.append(self)

        publishing.httpx.AsyncClient = CapturingAsyncClient
        try:
            image = await publishing.routerai_image('https://routerai.ru/api/v1', 'key', 'model', 'prompt')
        finally:
            publishing.httpx.AsyncClient = original_client

        self.assertEqual(image, b'image-bytes')
        self.assertEqual(clients[0].timeout, publishing.ROUTERAI_IMAGE_TIMEOUT_SECONDS)
        self.assertEqual(publishing.ROUTERAI_IMAGE_TIMEOUT_SECONDS, 900)


class MissingWordPressRestClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        request = httpx.Request('GET', url)
        return httpx.Response(404, request=request, text='not found')


class WordPressUploadTests(unittest.IsolatedAsyncioTestCase):
    def test_wordpress_api_base_accepts_site_or_api_urls(self):
        self.assertEqual(
            publishing.wordpress_api_base('https://example.com'),
            'https://example.com/wp-json/wp/v2',
        )
        self.assertEqual(
            publishing.wordpress_api_base('https://example.com/wp-json'),
            'https://example.com/wp-json/wp/v2',
        )
        self.assertEqual(
            publishing.wordpress_api_base('https://example.com/wp-json/wp/v2'),
            'https://example.com/wp-json/wp/v2',
        )

    async def test_upload_reports_missing_wordpress_rest_endpoint(self):
        site = type('Site', (), {
            'base_url': 'https://actu-congo.net',
            'username': 'user',
            'app_password': 'password',
            'default_status': 'draft',
            'categories': [],
        })()
        original_client = publishing.httpx.AsyncClient
        publishing.httpx.AsyncClient = MissingWordPressRestClient
        try:
            with self.assertRaises(publishing.WordPressUploadError) as ctx:
                await publishing.upload_to_wordpress(site, {'title': 'Title', 'content': 'Body'}, None, '')
        finally:
            publishing.httpx.AsyncClient = original_client

        message = str(ctx.exception)
        self.assertIn('WordPress REST API endpoint was not found', message)
        self.assertIn('https://actu-congo.net/wp-json/wp/v2', message)


class RetrospectiveSchedulingTests(unittest.TestCase):
    def test_retrospective_articles_for_same_day_share_publication_date(self):
        from app.worker import retrospective_scheduled_for

        scheduled_times = [retrospective_scheduled_for('2026-06-08') for _ in range(4)]

        self.assertEqual(len(set(scheduled_times)), 1)
        self.assertEqual(scheduled_times[0].isoformat(), '2026-06-08T00:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
