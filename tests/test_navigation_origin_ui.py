from pathlib import Path
import unittest


class NavigationOriginUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parent.parent / "web" / "navigation-extra.js"
        ).read_text(encoding="utf-8")

    def test_origin_dialog_offers_no_transfer(self):
        self.assertIn('data-origin="none">Без трансфера</button>', self.source)
        self.assertIn('sendOrigin("other", activeOriginRequest.destination, true)', self.source)
        self.assertIn('Оставил событие без трансфера.', self.source)

    def test_polling_does_not_reset_address_input_while_editing(self):
        self.assertIn('let originEditing = false;', self.source)
        self.assertIn(
            'if (originBusy || originEditing || optimizationBusy || activeOptimizationRequest || document.hidden) return;',
            self.source,
        )
        self.assertIn(
            'if (optimizationBusy || originBusy || originEditing || originDialogOpen() || document.hidden) return;',
            self.source,
        )
        self.assertIn(
            'if (activeOriginRequest?.event_id === request.event_id && !card.hidden) return;',
            self.source,
        )

    def test_repeated_render_preserves_typed_address(self):
        self.assertIn('if (wasHidden || previousChoice !== choice) input.value = "";', self.source)
        self.assertNotIn('input.value = "";\n    input.focus();', self.source)


if __name__ == "__main__":
    unittest.main()
