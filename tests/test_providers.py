"""Provider registry is a single source of truth."""
import unittest

from whisperfast.postprocess.providers import (
    PROVIDER_ORDER,
    PROVIDERS,
    provider_choices,
)


class TestProviderRegistry(unittest.TestCase):
    def test_order_and_choices_follow_providers(self):
        self.assertEqual(PROVIDER_ORDER, list(PROVIDERS))
        choices = provider_choices()
        self.assertEqual([pid for pid, _key in choices], PROVIDER_ORDER)
        for pid, label_key in choices:
            self.assertEqual(label_key, PROVIDERS[pid].label_key)


if __name__ == "__main__":
    unittest.main()
