import unittest
from market_labels import normalize_label

class MarketLabelTests(unittest.TestCase):
    def test_split_volvo_models_are_combined_from_explicit_titles(self):
        for title,raw in [('Volvo XC 90 2022 AWD','XC'),('Volvo XC-90 2022','XC-90'),('VOLVO XC90 2022','XC90')]:
            label=normalize_label(title,'Volvo',raw)
            self.assertEqual('XC90',label['model'])
            self.assertEqual('suv',label['category'])
            self.assertEqual('model_family',label['category_basis'])
        self.assertIsNone(normalize_label('Volvo XC','Volvo','XC')['model'])

    def test_brand_and_model_aliases_do_not_turn_promotion_text_into_a_brand(self):
        label=normalize_label('AKCIJA Peugeot 3008,2021','Akcija','Peugeot')
        self.assertEqual(('Peugeot','3008'),(label['make'],label['model']))
        self.assertEqual('Golf',normalize_label('GOLF 7 1.6 TDI','Golf','7')['model'])
        self.assertIsNone(normalize_label('Prodajem automobil','Prodajem','automobil')['make'])
        self.assertIsNone(normalize_label('Peugeot 2013 130 ks','Peugeot','2013')['model'])
        self.assertIsNone(normalize_label('BMW 374 KS','BMW','374')['model'])

    def test_unknown_body_style_is_not_assigned_from_a_multi_body_model(self):
        self.assertIsNone(normalize_label('Audi A4 2022','Audi','A4')['category'])
        self.assertEqual('estate',normalize_label('Audi A4 Avant 2022','Audi','A4')['category'])
        self.assertEqual('title_keyword',normalize_label('BMW 320 sedan','BMW','320')['category_basis'])
