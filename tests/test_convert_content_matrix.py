from __future__ import annotations

import unittest
from typing import Any

import pikepdf

from spotpdf.content_support import instruction, operator_name
from spotpdf.convert_content import ConversionContentPlanner
from spotpdf.model import InvalidPdfError, UnsupportedSpotUseError
from tests.conversion_fixtures import separation


class ConversionOperatorMatrixTests(unittest.TestCase):
    def test_path_and_clipping_operator_matrix_is_preserved(self) -> None:
        paint_operators = ("f", "F", "f*", "S", "s", "B", "B*", "b", "b*", "n")
        path = (
            instruction("m", 0, 0),
            instruction("l", 10, 0),
            instruction("c", 10, 1, 10, 9, 10, 10),
            instruction("v", 9, 10, 8, 10),
            instruction("y", 1, 10, 0, 0),
            instruction("h"),
            instruction("re", 0, 0, 10, 10),
            instruction("W"),
            instruction("W*"),
        )
        for paint in paint_operators:
            with self.subTest(paint=paint):
                original = (
                    instruction("cs", pikepdf.Name.Ink),
                    instruction("CS", pikepdf.Name.Ink),
                    *path,
                    instruction(paint),
                )
                result = self._planner().rewrite(original)
                self.assertEqual(
                    self._signatures(result.instructions[2:]),
                    self._signatures(original[2:]),
                )
                self.assertEqual(
                    tuple(operator_name(item) for item in result.instructions[:2]),
                    ("k", "K"),
                )

    def test_all_text_render_modes_and_show_operators_are_preserved(self) -> None:
        shows = (
            instruction("Tj", pikepdf.String("Tj")),
            instruction("TJ", pikepdf.Array([pikepdf.String("TJ"), -20])),
            instruction("'", pikepdf.String("quote")),
            instruction('"', 1, 2, pikepdf.String("double quote")),
        )
        for mode in range(8):
            for show in shows:
                with self.subTest(mode=mode, show=operator_name(show)):
                    original = (
                        instruction("cs", pikepdf.Name.Ink),
                        instruction("CS", pikepdf.Name.Ink),
                        instruction("BT"),
                        instruction("Tf", pikepdf.Name.F1, 12),
                        instruction("Tr", mode),
                        show,
                        instruction("ET"),
                    )
                    result = self._planner().rewrite(original)
                    self.assertEqual(
                        self._signatures(result.instructions[2:]),
                        self._signatures(original[2:]),
                    )

    def test_text_requires_supported_font_and_knockout_semantics(self) -> None:
        no_font = (
            instruction("cs", pikepdf.Name.Ink),
            instruction("BT"),
            instruction("Tj", pikepdf.String("text")),
            instruction("ET"),
        )
        with self.assertRaisesRegex(InvalidPdfError, "no valid font"):
            self._planner().rewrite(no_font)

        resources = self._resources()
        resources.Font.F1.Subtype = pikepdf.Name.Type3
        type_three = (
            instruction("cs", pikepdf.Name.Ink),
            instruction("BT"),
            instruction("Tf", pikepdf.Name.F1, 12),
            instruction("Tj", pikepdf.String("text")),
            instruction("ET"),
        )
        with self.assertRaisesRegex(UnsupportedSpotUseError, "Type 3"):
            self._planner(resources).rewrite(type_three)

        knockout = self._resources()
        knockout.ExtGState = pikepdf.Dictionary(NoKnockout=pikepdf.Dictionary(TK=False))
        with self.assertRaisesRegex(UnsupportedSpotUseError, "non-knockout"):
            self._planner(knockout).rewrite(
                (
                    instruction("gs", pikepdf.Name.NoKnockout),
                    *type_three[:1],
                    instruction("BT"),
                    instruction("Tf", pikepdf.Name.F1, 12),
                    instruction("Tj", pikepdf.String("text")),
                    instruction("ET"),
                )
            )

    def test_neutral_graphics_state_is_accepted_and_unsafe_state_is_rejected(self) -> None:
        neutral = self._resources()
        neutral.ExtGState = pikepdf.Dictionary(
            Safe=pikepdf.Dictionary(
                OP=False,
                op=False,
                OPM=1,
                CA=1,
                ca=1,
                BM=pikepdf.Array([pikepdf.Name.Normal, pikepdf.Name.Compatible]),
                SMask=pikepdf.Name("/None"),
                TK=True,
            )
        )
        result = self._planner(neutral).rewrite(
            (
                instruction("gs", pikepdf.Name.Safe),
                instruction("cs", pikepdf.Name.Ink),
                instruction("f"),
            )
        )
        self.assertTrue(result.changed)

        cases = {
            "alpha": pikepdf.Dictionary(ca=0.5),
            "blend": pikepdf.Dictionary(BM=pikepdf.Name.Multiply),
            "overprint": pikepdf.Dictionary(op=True),
            "soft mask": pikepdf.Dictionary(SMask=pikepdf.Dictionary(S=pikepdf.Name.Alpha)),
        }
        for label, parameters in cases.items():
            with self.subTest(label=label):
                resources = self._resources()
                resources.ExtGState = pikepdf.Dictionary(Unsafe=parameters)
                with self.assertRaises(UnsupportedSpotUseError):
                    self._planner(resources).rewrite(
                        (
                            instruction("gs", pikepdf.Name.Unsafe),
                            instruction("cs", pikepdf.Name.Ink),
                            instruction("f"),
                        )
                    )

    def test_compatibility_section_and_unknown_xobject_fail_closed_under_target(self) -> None:
        with self.assertRaisesRegex(UnsupportedSpotUseError, "compatibility"):
            self._planner().rewrite(
                (
                    instruction("cs", pikepdf.Name.Ink),
                    instruction("BX"),
                    instruction("EX"),
                )
            )

        with pikepdf.Pdf.new() as pdf:
            postscript = pdf.make_stream(b"%!PS")
            postscript.Subtype = pikepdf.Name.PS
            resources = self._resources()
            resources.XObject = pikepdf.Dictionary(PS=postscript)
            with self.assertRaisesRegex(UnsupportedSpotUseError, "XObject subtype"):
                self._planner(resources).rewrite(
                    (
                        instruction("cs", pikepdf.Name.Ink),
                        instruction("Do", pikepdf.Name.PS),
                    )
                )

    def test_unknown_content_operators_fail_closed_outside_compatibility_sections(self) -> None:
        cases = (
            (
                instruction("vendorState"),
                instruction("cs", pikepdf.Name.Ink),
                instruction("f"),
            ),
            (
                instruction("cs", pikepdf.Name.Ink),
                instruction("vendorPaint"),
            ),
        )
        for instructions in cases:
            with (
                self.subTest(operator=operator_name(instructions[0])),
                self.assertRaisesRegex(UnsupportedSpotUseError, "unknown content operator"),
            ):
                self._planner().rewrite(instructions)

    def test_unknown_operator_inside_compatibility_section_is_preserved(self) -> None:
        original = (
            instruction("BX"),
            instruction("vendorNoop"),
            instruction("EX"),
            instruction("cs", pikepdf.Name.Ink),
            instruction("scn", 0.5),
            instruction("re", 0, 0, 10, 10),
            instruction("f"),
        )

        result = self._planner().rewrite(original)

        self.assertEqual(
            tuple(operator_name(item) for item in result.instructions),
            ("BX", "vendorNoop", "EX", "k", "k", "re", "f"),
        )

    def test_operators_outside_their_graphics_object_context_fail_closed(self) -> None:
        cases = (
            ("path-in-text", (instruction("BT"), instruction("re", 0, 0, 10, 10))),
            ("xobject-in-text", (instruction("BT"), instruction("Do", pikepdf.Name.X))),
            ("shading-in-text", (instruction("BT"), instruction("sh", pikepdf.Name.Shade))),
            ("text-position-outside", (instruction("Td", 10, 10),)),
            ("type3-width-on-page", (instruction("d0", 1000, 0),)),
        )
        for label, instructions in cases:
            with self.subTest(context=label), self.assertRaises(InvalidPdfError):
                self._planner().rewrite(instructions)

    def test_ext_gstate_font_updates_the_active_text_font(self) -> None:
        resources = self._resources()
        type_three = pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type3,
            Resources=pikepdf.Dictionary(),
        )
        resources.ExtGState = pikepdf.Dictionary(
            Switch=pikepdf.Dictionary(Font=pikepdf.Array([type_three, 12]))
        )

        with self.assertRaisesRegex(UnsupportedSpotUseError, "Type 3"):
            self._planner(resources).rewrite(
                (
                    instruction("cs", pikepdf.Name.Ink),
                    instruction("BT"),
                    instruction("Tf", pikepdf.Name.F1, 12),
                    instruction("gs", pikepdf.Name.Switch),
                    instruction("Tj", pikepdf.String("text")),
                    instruction("ET"),
                )
            )

    def test_malformed_ext_gstate_font_is_rejected(self) -> None:
        resources = self._resources()
        resources.ExtGState = pikepdf.Dictionary(
            Bad=pikepdf.Dictionary(Font=pikepdf.Array([pikepdf.Name.F1, 12]))
        )

        with self.assertRaisesRegex(InvalidPdfError, "ExtGState /Font"):
            self._planner(resources).rewrite((instruction("gs", pikepdf.Name.Bad),))

    def _planner(
        self,
        resources: pikepdf.Dictionary | None = None,
    ) -> ConversionContentPlanner:
        return ConversionContentPlanner(
            resources or self._resources(),
            "DemoSpot",
            (0.0, 0.8, 1.0, 0.0),
            "operator matrix",
        )

    @staticmethod
    def _resources() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(Subtype=pikepdf.Name.Type1)),
        )

    @staticmethod
    def _signatures(instructions: tuple[Any, ...]) -> tuple[bytes, ...]:
        return tuple(pikepdf.unparse_content_stream([item]) for item in instructions)


if __name__ == "__main__":
    unittest.main()
