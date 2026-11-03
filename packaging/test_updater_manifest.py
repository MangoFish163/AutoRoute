import base64
import unittest
from updater_manifest import manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.signature = base64.b64encode(b'untrusted comment: test\nsignature\ntrusted comment: test\nglobal\n').decode()

    def test_immutable_asset_url_and_signature_content(self):
        result = manifest('owner/repo', 'v0.1.1-unsigned.3', '0.1.1', self.signature, '修复更新')
        asset = result['platforms']['windows-x86_64']
        self.assertEqual(asset['signature'], self.signature)
        self.assertEqual(asset['url'], 'https://github.com/owner/repo/releases/download/v0.1.1-unsigned.3/win-x64-setup.exe')
        self.assertEqual(result['notes'], '修复更新')

    def test_mismatched_or_unsafe_tags_and_repository_are_rejected(self):
        for repo, tag in [('owner/repo', 'v0.2.0'), ('owner/repo', '../v0.1.1'), ('evil/repo/extra', 'v0.1.1')]:
            with self.assertRaises(ValueError):
                manifest(repo, tag, '0.1.1', self.signature, '')

    def test_signature_url_cannot_replace_signature_content(self):
        with self.assertRaises(ValueError):
            manifest('owner/repo', 'v0.1.1', '0.1.1', 'https://example.com/file.sig', '')


if __name__ == '__main__':
    unittest.main()
