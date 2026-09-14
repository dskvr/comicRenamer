import contextlib
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from comicvine import ComicVine


def page(results, total=None):
    return io.BytesIO(json.dumps({'status_code': 1, 'results': results,
                                 'number_of_total_results': len(results) if total is None else total}).encode())


def volume(name='Saga', year='2012', identifier=1):
    return {'id': identifier, 'name': name, 'start_year': year}


class ComicVineTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.sleep = patch('comicvine.time.sleep').start()
        self.clock = patch('comicvine.time.monotonic', return_value=100).start()
        self.http = patch('comicvine.urlopen').start()
        self.addCleanup(patch.stopall)
        self.client = ComicVine('secret-test-key')

    def test_exact_match_request_and_cache(self):
        self.http.return_value = page([volume('Saga & Friends')])
        self.assertEqual(self.client.lookup_year('Saga & Friends'), '2012')
        self.assertEqual(self.client.lookup_year('saga & friends'), '2012')
        self.http.assert_called_once()
        request = self.http.call_args[0][0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(query['filter'], ['name:Saga & Friends'])
        self.assertEqual(query['api_key'], ['secret-test-key'])
        self.assertEqual(self.http.call_args[1]['timeout'], 20)
        self.assertIn('comicRenamer', request.get_header('User-agent'))
        self.assertNotIn('secret-test-key', self.output.getvalue())

    def test_no_exact_ambiguous_or_missing_year_does_not_guess(self):
        for results in ([], [volume('Saga Companion')], [volume(), volume(identifier=2)],
                        [volume(year=None)], [volume(year='unknown')]):
            with self.subTest(results=results):
                self.http.reset_mock()
                self.http.return_value = page(results)
                client = ComicVine('test')
                self.assertIsNone(client.lookup_year('Saga'))
                self.assertIsNone(client.lookup_year('Saga'))
                self.http.assert_called_once()

    def test_later_page_can_make_match_ambiguous(self):
        self.http.side_effect = [page([volume()], 2), page([volume(year='2020', identifier=2)], 2)]
        self.assertIsNone(self.client.lookup_year('Saga'))
        second_query = parse_qs(urlparse(self.http.call_args_list[1][0][0].full_url).query)
        self.assertEqual(second_query['offset'], ['1'])
        self.sleep.assert_called_once()
        self.assertAlmostEqual(self.sleep.call_args[0][0], 18.1)

    def test_unique_match_on_later_page(self):
        self.http.side_effect = [page([volume('Saga Companion')], 2), page([volume()], 2)]
        self.assertEqual(self.client.lookup_year('Saga'), '2012')

    def test_incomplete_page_does_not_accept_early_match(self):
        self.http.side_effect = [page([volume()], 2), page([], 2)]
        self.assertIsNone(self.client.lookup_year('Saga'))
        self.assertTrue(self.client.disabled)

    def test_search_page_bound_does_not_accept_partial_result(self):
        self.http.side_effect = [page([volume()], 1000) for _ in range(5)]
        self.assertIsNone(self.client.lookup_year('Saga'))
        self.assertEqual(self.http.call_count, 5)

    def test_failures_stop_requests_and_redact_secrets(self):
        failures = [HTTPError('https://example.com/?api_key=secret-test-key', 429,
                              'secret-test-key', {}, None), TimeoutError('secret-test-key')]
        for failure in failures:
            with self.subTest(failure=type(failure)):
                self.http.reset_mock()
                self.http.side_effect = failure
                client = ComicVine('secret-test-key')
                self.assertIsNone(client.lookup_year('Saga'))
                self.assertIsNone(client.lookup_year('Other'))
                self.http.assert_called_once()
        self.assertNotIn('secret-test-key', self.output.getvalue())

    def test_api_errors_and_malformed_responses_disable_requests(self):
        for data in ({'status_code': 100}, {'status_code': 1, 'results': None}, [], 'not JSON'):
            with self.subTest(data=data):
                self.http.return_value = io.BytesIO((data if isinstance(data, str) else json.dumps(data)).encode())
                client = ComicVine('test')
                self.assertIsNone(client.lookup_year('Saga'))
                self.assertTrue(client.disabled)

    def test_empty_key_never_requests(self):
        self.assertIsNone(ComicVine('').lookup_year('Saga'))
        self.http.assert_not_called()


if __name__ == '__main__':
    unittest.main()
