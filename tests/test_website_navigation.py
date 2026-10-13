import json
import unittest
from unittest.mock import Mock
from selenium.common.exceptions import TimeoutException
from scrapers.olx_website_collector import WebsiteBlocked, WebsiteBrowser


class NavigationTests(unittest.TestCase):
    def test_navigation_timeout_can_use_ready_dom_without_another_visit(self):
        browser = WebsiteBrowser()
        browser.driver = Mock()
        browser.driver.get.side_effect = TimeoutException()
        browser.driver.get_log.return_value = []
        page = {'blocked':False, 'result_heading':'Pronađeno 39 rezultata', 'attributes':{}, 'headings':[]}
        browser.driver.execute_script.return_value = page
        self.assertEqual(page, browser.read('https://olx.ba/pretraga', 'search'))
        browser.driver.get.assert_called_once()

    def test_refusal_after_navigation_timeout_still_stops(self):
        browser = WebsiteBrowser()
        browser.driver = Mock()
        browser.driver.get.side_effect = TimeoutException()
        response = {'message':json.dumps({'message':{'method':'Network.responseReceived',
                     'params':{'type':'Document','response':{'url':'https://olx.ba/pretraga','status':403}}}})}
        browser.driver.get_log.side_effect = [[],[response]]
        with self.assertRaises(WebsiteBlocked):
            browser.read('https://olx.ba/pretraga','search')
