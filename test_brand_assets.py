"""Check generated icon containers without a runtime image-library dependency."""
from pathlib import Path
import struct
import unittest


class BrandAssetsTests(unittest.TestCase):
    def test_header_png_dimensions(self):
        data = Path('assets/graild-logo.png').read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', data[16:24]), (256, 256))

    def test_windows_icon_has_small_and_high_resolution_sizes(self):
        data = Path('assets/graild.ico').read_bytes()
        reserved, kind, count = struct.unpack('<HHH', data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        sizes = set()
        for index in range(count):
            width, height, _, _, _, _, length, offset = struct.unpack('<BBBBHHII', data[6+16*index:22+16*index])
            sizes.add((width or 256, height or 256))
            self.assertLessEqual(offset + length, len(data))
        self.assertTrue({(16,16),(32,32),(48,48),(256,256)} <= sizes)

    def test_mac_icon_contains_retina_payloads(self):
        data = Path('assets/graild.icns').read_bytes()
        self.assertEqual(data[:4], b'icns')
        self.assertEqual(struct.unpack('>I', data[4:8])[0], len(data))
        chunks = set()
        offset = 8
        while offset < len(data):
            kind = data[offset:offset+4]
            size = struct.unpack('>I', data[offset+4:offset+8])[0]
            self.assertGreaterEqual(size, 8)
            chunks.add(kind)
            offset += size
        self.assertEqual(offset, len(data))
        self.assertIn(b'ic10', chunks)


if __name__ == '__main__':
    unittest.main()
