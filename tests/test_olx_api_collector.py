import unittest

from scrapers.olx_api_collector import CollectionIntegrityError, OLXAPICollector, build_source_query


def listing(listing_id, title='VW Polo 1.6 TDI 2010', price=6999):
    return {
        'id': listing_id,
        'title': title,
        'price': price,
        'date': 1782122064,
        'location': 'Sarajevo',
        'user_type': 'shop',
        'special_labels': [
            {'label': 'Gorivo', 'value': 'dizel'},
            {'label': 'Kilometraža', 'value': '230.000'},
            {'label': 'Godište', 'value': 2010},
        ],
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append((url, params))
        return FakeResponse({'data': self.results})


class OLXAPICollectorTests(unittest.TestCase):
    def test_collects_single_api_window_with_provenance(self):
        session = FakeSession([listing(101), listing(102, 'Audi A6 2013', 12000)])
        collector = OLXAPICollector(session=session)

        results = collector.collect(pages=2)

        self.assertEqual(['101', '102'], [item['listing_id'] for item in results])
        self.assertEqual([1, 1], [item['source_page'] for item in results])
        self.assertTrue(all(item['source_query'] == build_source_query(40) for item in results))
        self.assertTrue(all(item['source_per_page'] == 40 for item in results))
        self.assertEqual(1, len(session.calls))
        self.assertEqual(40, session.calls[0][1]['per_page'])
        self.assertEqual('Volkswagen', results[0]['make'])
        self.assertEqual('Polo', results[0]['model'])
        self.assertEqual(230000, results[0]['mileage'])
        self.assertEqual(1, len(collector.page_stats))
        self.assertEqual(2, collector.page_stats[0]['distinct_ids'])

    def test_deduplicates_repeated_raw_ids_without_failing(self):
        session = FakeSession([listing(101), listing(101, 'VW GOLF 2012', 9000)])

        collector = OLXAPICollector(session=session)
        results = collector.collect(pages=2)

        self.assertEqual(['101'], [item['listing_id'] for item in results])
        self.assertEqual(1, collector.page_stats[0]['duplicate_ids'])
        self.assertEqual(1, collector.page_stats[0]['distinct_ids'])

    def test_skips_implausible_prices(self):
        session = FakeSession([listing(101, price=5)])

        with self.assertRaises(CollectionIntegrityError):
            OLXAPICollector(session=session).collect(pages=1)


if __name__ == '__main__':
    unittest.main()
