import unittest

import discord

from embed_template import BRAND_NAME, DEFAULT_FOOTER, error_embed, info_embed, success_embed


class TestEmbedTemplate(unittest.TestCase):
    def test_default_embed_has_shared_branding(self):
        embed = info_embed("Example", "Details")

        self.assertEqual(embed.title, "Example")
        self.assertEqual(embed.description, "Details")
        self.assertEqual(embed.author.name, BRAND_NAME)
        self.assertEqual(embed.footer.text, DEFAULT_FOOTER)

    def test_status_variants_use_distinct_colors(self):
        self.assertNotEqual(info_embed("Info").colour, success_embed("Success").colour)
        self.assertNotEqual(success_embed("Success").colour, error_embed("Error").colour)
        self.assertIsInstance(error_embed("Error").colour, discord.Colour)

    def test_requester_footer_uses_username(self):
        class User:
            name = "ExampleUser"

        embed = info_embed("Example", requested_by=User())

        self.assertEqual(embed.footer.text, "Requested by ExampleUser")


if __name__ == "__main__":
    unittest.main()