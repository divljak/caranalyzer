import unittest
from unittest.mock import Mock
from scrapers.olx_listing_details import DetailsUnavailable, fetch_transmission, normalize_transmission, parse_transmission


class ListingDetailsTests(unittest.TestCase):
    def test_only_explicit_transmission_values_are_normalized(self):
        for raw, expected in [('Automatik','automatic'),(' Manuelni ','manual'),('RuČni','manual'),('Automatic','automatic'),('Unknown',None),('Automatska klima',None),('DSG?',None),('',None),(True,None)]:
            with self.subTest(raw=raw):
                self.assertEqual(expected,normalize_transmission(raw))

    def test_structured_attribute_is_used_without_inference_from_title_or_model(self):
        payload={'id':123,'category_id':18,'title':'Volvo XC60 automatic','attributes':[{'attr_code':'transmisija','value':'Manuelni'}]}
        self.assertEqual({'transmission':'manual','raw_value':'Manuelni'},parse_transmission(payload,'123'))
        payload['attributes']=[]
        self.assertIsNone(parse_transmission(payload,'123')['transmission'])
        payload['attributes']=[{'attr_code':'transmisija','value':'Automatik'},{'attr_code':'transmisija','value':'Manuelni'}]
        self.assertIsNone(parse_transmission(payload,'123')['transmission'])

    def test_mismatched_id_and_non_car_payloads_are_rejected(self):
        for payload in [{'id':124,'category_id':18},{'id':123,'category_id':19},{'id':123},[]]:
            with self.assertRaises(ValueError):parse_transmission(payload,'123')

    def test_fetch_keeps_only_the_transmission_evidence_and_respects_http_errors(self):
        session=Mock()
        response=session.get.return_value
        response.status_code=200
        response.json.return_value={'id':123,'category_id':18,'user':{'private':'not retained'},'attributes':[{'attr_code':'transmisija','value':'Automatik'}]}
        self.assertEqual({'transmission':'automatic','raw_value':'Automatik','source_url':'https://api.olx.ba/listings/123'},fetch_transmission(session,'123'))
        for code in [404,403,429,500]:
            response.status_code=code
            with self.assertRaises(DetailsUnavailable) as caught:fetch_transmission(session,'123')
            self.assertEqual(code,caught.exception.status_code)
        session.reset_mock()
        with self.assertRaises(ValueError):fetch_transmission(session,'../secret')
        session.get.assert_not_called()
