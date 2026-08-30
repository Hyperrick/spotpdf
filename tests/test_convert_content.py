from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

import pikepdf

from spotpdf.cmyk import scale_cmyk_tint, validate_cmyk_percentages
from spotpdf.content_support import instruction, operator_name
from spotpdf.convert_content import ConversionContentPlanner
from spotpdf.convert_state import ConversionGraphicsState
from spotpdf.model import InvalidPdfError, UnsupportedSpotUseError


class CmykTintTests(unittest.TestCase):
    def test_scales_requested_recipe_for_every_tint_endpoint(self) -> None:
        recipe = (0.0, 0.8, 1.0, 0.2)
        cases = (
            (0, (0.0, 0.0, 0.0, 0.0)),
            (Decimal("0.25"), (0.0, 0.2, 0.25, 0.05)),
            (0.5, (0.0, 0.4, 0.5, 0.1)),
            (1, recipe),
        )
        for tint, expected in cases:
            with self.subTest(tint=tint):
                actual = tuple(float(value) for value in scale_cmyk_tint(tint, recipe))
                self.assertEqual(actual, expected)

    def test_rejects_invalid_tints_instead_of_clamping(self) -> None:
        invalid = (
            -0.01,
            1.01,
            Decimal("-1e-5000"),
            Decimal("1.0000000000000000001"),
            Decimal("sNaN"),
            float("nan"),
            float("inf"),
            True,
            "0.5",
        )
        for tint in invalid:
            with self.subTest(tint=tint), self.assertRaises(InvalidPdfError):
                scale_cmyk_tint(tint, (0.0, 0.8, 1.0, 0.0))

    def test_percentage_bounds_are_compared_before_float_rounding(self) -> None:
        invalid = (
            Decimal("-1e-5000"),
            Decimal("100.0000000000000001"),
            Decimal("sNaN"),
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InvalidPdfError):
                validate_cmyk_percentages((value, 0, 0, 0))


class ConversionContentPlannerTests(unittest.TestCase):
    recipe = (0.0, 0.8, 1.0, 0.0)

    def test_rewrites_initial_and_explicit_nonstroking_tints(self) -> None:
        result = self._rewrite(
            instruction("cs", pikepdf.Name.Ink),
            instruction("scn", Decimal("0.25")),
            instruction("re", 0, 0, 10, 10),
            instruction("f"),
        )

        self.assertEqual(self._operations(result.instructions), ("k", "k", "re", "f"))
        self.assertEqual(self._numbers(result.instructions[0]), self.recipe)
        self.assertEqual(self._numbers(result.instructions[1]), (0.0, 0.2, 0.25, 0.0))
        self.assertEqual(result.color_operators_rewritten, 2)
        self.assertEqual(result.target_paint_operations, 1)

    def test_rewrites_stroking_tints_with_uppercase_process_operator(self) -> None:
        result = self._rewrite(
            instruction("CS", pikepdf.Name.Ink),
            instruction("SCN", Decimal("0.5")),
            instruction("m", 0, 0),
            instruction("l", 10, 10),
            instruction("S"),
        )

        self.assertEqual(self._operations(result.instructions), ("K", "K", "m", "l", "S"))
        self.assertEqual(self._numbers(result.instructions[1]), (0.0, 0.4, 0.5, 0.0))
        self.assertEqual(result.target_paint_operations, 1)

    def test_q_q_restores_target_selection_and_direct_colors_clear_it(self) -> None:
        result = self._rewrite(
            instruction("cs", pikepdf.Name.Ink),
            instruction("q"),
            instruction("rg", 1, 0, 0),
            instruction("f"),
            instruction("Q"),
            instruction("f"),
            instruction("k", 0, 0, 0, 1),
            instruction("f"),
        )

        self.assertEqual(result.target_paint_operations, 1)
        self.assertEqual(
            self._operations(result.instructions),
            ("k", "q", "rg", "f", "Q", "f", "k", "f"),
        )

    def test_nested_q_q_restores_independent_fill_and_stroke_state(self) -> None:
        result = self._rewrite(
            instruction("cs", pikepdf.Name.Ink),
            instruction("CS", pikepdf.Name.Ink),
            instruction("q"),
            instruction("scn", Decimal("0.25")),
            instruction("RG", 1, 0, 0),
            instruction("q"),
            instruction("rg", 1, 0, 0),
            instruction("CS", pikepdf.Name.Ink),
            instruction("SCN", Decimal("0.5")),
            instruction("B"),
            instruction("Q"),
            instruction("B"),
            instruction("Q"),
            instruction("B"),
        )

        self.assertEqual(
            self._operations(result.instructions),
            ("k", "K", "q", "k", "RG", "q", "rg", "K", "K", "B", "Q", "B", "Q", "B"),
        )
        colors = [
            (operator_name(item), self._numbers(item))
            for item in result.instructions
            if operator_name(item) in {"k", "K"}
        ]
        self.assertEqual(
            colors,
            [
                ("k", self.recipe),
                ("K", self.recipe),
                ("k", (0.0, 0.2, 0.25, 0.0)),
                ("K", self.recipe),
                ("K", (0.0, 0.4, 0.5, 0.0)),
            ],
        )
        self.assertEqual(result.color_operators_rewritten, 5)
        self.assertEqual(result.target_paint_operations, 4)

    def test_target_sc_and_sc_are_rejected(self) -> None:
        for select, set_color in (("cs", "sc"), ("CS", "SC")):
            with self.subTest(operator=set_color), self.assertRaises(UnsupportedSpotUseError):
                self._rewrite(
                    instruction(select, pikepdf.Name.Ink),
                    instruction(set_color, Decimal("0.5")),
                )

    def test_default_cmyk_override_is_rejected(self) -> None:
        resources = self._resources()
        resources.ColorSpace.DefaultCMYK = pikepdf.Name.DeviceCMYK
        with self.assertRaisesRegex(UnsupportedSpotUseError, "DefaultCMYK"):
            self._rewrite(instruction("cs", pikepdf.Name.Ink), resources=resources)

    def test_inherited_target_tint_also_rejects_default_cmyk(self) -> None:
        resources = self._resources()
        resources.ColorSpace.DefaultCMYK = pikepdf.Name.DeviceCMYK
        state = ConversionGraphicsState()
        state.nonstroking.target_selected = True
        planner = ConversionContentPlanner(
            resources,
            "DemoSpot",
            self.recipe,
            "inherited form",
        )
        with self.assertRaisesRegex(UnsupportedSpotUseError, "DefaultCMYK"):
            planner.rewrite((instruction("scn", Decimal("0.5")),), state)

    def test_effective_overprint_is_rejected_only_when_target_paints(self) -> None:
        resources = self._resources()
        resources.ExtGState = pikepdf.Dictionary(Over=pikepdf.Dictionary(OP=True))
        with self.assertRaisesRegex(UnsupportedSpotUseError, "overprint"):
            self._rewrite(
                instruction("gs", pikepdf.Name.Over),
                instruction("cs", pikepdf.Name.Ink),
                instruction("f"),
                resources=resources,
            )

    def test_none_soft_mask_is_neutral_but_active_mask_is_rejected(self) -> None:
        neutral = self._resources()
        neutral.ExtGState = pikepdf.Dictionary(
            ClearMask=pikepdf.Dictionary(SMask=pikepdf.Name("/None"))
        )
        result = self._rewrite(
            instruction("gs", pikepdf.Name.ClearMask),
            instruction("cs", pikepdf.Name.Ink),
            instruction("f"),
            resources=neutral,
        )
        self.assertTrue(result.changed)

        active = self._resources()
        active.ExtGState = pikepdf.Dictionary(
            Masked=pikepdf.Dictionary(SMask=pikepdf.Dictionary(S=pikepdf.Name.Alpha))
        )
        with self.assertRaisesRegex(UnsupportedSpotUseError, "soft-masked"):
            self._rewrite(
                instruction("gs", pikepdf.Name.Masked),
                instruction("cs", pikepdf.Name.Ink),
                instruction("f"),
                resources=active,
            )

    def test_alpha_must_be_exactly_opaque(self) -> None:
        resources = self._resources()
        resources.ExtGState = pikepdf.Dictionary(
            AlmostOpaque=pikepdf.Dictionary(ca=Decimal("0.999999"))
        )
        with self.assertRaisesRegex(UnsupportedSpotUseError, "non-opaque"):
            self._rewrite(
                instruction("gs", pikepdf.Name.AlmostOpaque),
                instruction("cs", pikepdf.Name.Ink),
                instruction("f"),
                resources=resources,
            )

    def test_malformed_direct_color_cannot_silently_clear_target_state(self) -> None:
        with self.assertRaisesRegex(InvalidPdfError, "malformed rg"):
            self._rewrite(
                instruction("cs", pikepdf.Name.Ink),
                instruction("rg", 1, 0),
                instruction("f"),
            )

    def _rewrite(
        self,
        *instructions: Any,
        resources: pikepdf.Dictionary | None = None,
    ):
        planner = ConversionContentPlanner(
            resources or self._resources(),
            "DemoSpot",
            self.recipe,
            "test stream",
        )
        return planner.rewrite(instructions)

    @staticmethod
    def _resources() -> pikepdf.Dictionary:
        tint = pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array([0, 0.8, 1, 0]),
            N=1,
        )
        separation = pikepdf.Array(
            [pikepdf.Name.Separation, pikepdf.Name.DemoSpot, pikepdf.Name.DeviceCMYK, tint]
        )
        return pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation))

    @staticmethod
    def _operations(instructions: tuple[Any, ...]) -> tuple[str, ...]:
        return tuple(operator_name(item) for item in instructions)

    @staticmethod
    def _numbers(item: Any) -> tuple[float, ...]:
        return tuple(float(value) for value in item.operands)


if __name__ == "__main__":
    unittest.main()
