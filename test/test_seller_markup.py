"""Seller dialogs must retain their forms when parsed by a browser."""
from html.parser import HTMLParser
from pathlib import Path
import unittest


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.open_form = None
        self.forms = []
        self.nested = []

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            form_id = dict(attrs).get('id')
            if self.open_form is not None:
                self.nested.append((self.open_form, form_id))
            self.open_form = form_id or '(unnamed)'
            self.forms.append(form_id)

    def handle_endtag(self, tag):
        if tag == 'form':
            self.open_form = None


class SellerMarkupTests(unittest.TestCase):
    def test_dashboard_forms_are_independent(self):
        parser = FormParser()
        parser.feed((Path(__file__).resolve().parents[1] / 'seller-dashboard.html').read_text())
        self.assertEqual(parser.nested, [], 'Nested forms are dropped by browsers and stop dashboard initialization')
        for form_id in ('product-form', 'stock-form', 'support-form', 'payment-form', 'chat-compose'):
            self.assertEqual(parser.forms.count(form_id), 1)


if __name__ == '__main__':
    unittest.main()
