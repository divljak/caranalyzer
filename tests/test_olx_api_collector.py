import unittest

from scrapers.olx_api_collector import CollectionIntegrityError, OLXAPICollector, SOURCE_QUERY


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
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append((url, params))
        return FakeResponse({'data': self.pages[params['page']]})


class OLXAPICollectorTests(unittest.TestCase):
    def test_collects_distinct_pages_with_provenance(self):
        session = FakeSession({1: [listing(101)], 2: [listing(102, 'Audi A6 2013', 12000)]})
        collector = OLXAPICollector(session=session)

        results = collector.collect(pages=2)

        self.assertEqual(['101', '102'], [item['listing_id'] for item in results])
        self.assertEqual([1, 2], [item['source_page'] for item in results])
        self.assertTrue(all(item['source_query'] == SOURCE_QUERY for item in results))
        self.assertEqual('Volkswagen', results[0]['make'])
        self.assertEqual('Polo', results[0]['model'])
        self.assertEqual(230000, results[0]['mileage'])
        self.assertEqual(2, len(collector.page_stats))

    def test_rejects_duplicate_ids_across_pages(self):
        session = FakeSession({1: [listing(101)], 2: [listing(101, 'VW GOLF 2012', 9000)]})

        with self.assertRaises(CollectionIntegrityError):
            OLXAPICollector(session=session).collect(pages=2)

    def test_skips_implausible_prices(self):
        session = FakeSession({1: [listing(101, price=5)]})

        with self.assertRaises(CollectionIntegrityError):
            OLXAPICollector(session=session).collect(pages=1)


if __name__ == '__main__':
    unittest.main()
