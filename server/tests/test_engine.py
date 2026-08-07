"""Unit tests for card geometry and win detection."""

from app.engine import free_position, pattern_label, winning_lines


class TestGeometry:
    def test_free_space_is_the_centre(self):
        assert free_position(5) == 12
        assert free_position(3) == 4
        assert free_position(7) == 24

    def test_line_count(self):
        lines = winning_lines(5)
        # 5 rows + 5 columns + 2 diagonals + corners + blackout
        assert len(lines) == 14

    def test_rows_are_contiguous(self):
        assert winning_lines(5)["row-0"] == [0, 1, 2, 3, 4]
        assert winning_lines(5)["row-4"] == [20, 21, 22, 23, 24]

    def test_columns_stride_by_size(self):
        assert winning_lines(5)["col-0"] == [0, 5, 10, 15, 20]
        assert winning_lines(5)["col-4"] == [4, 9, 14, 19, 24]

    def test_diagonals(self):
        lines = winning_lines(5)
        assert lines["diag-main"] == [0, 6, 12, 18, 24]
        assert lines["diag-anti"] == [4, 8, 12, 16, 20]

    def test_corners_and_blackout(self):
        lines = winning_lines(5)
        assert lines["corners"] == [0, 4, 20, 24]
        assert len(lines["blackout"]) == 25

    def test_every_line_has_the_right_length(self):
        for name, cells in winning_lines(5).items():
            if name == "corners":
                assert len(cells) == 4
            elif name == "blackout":
                assert len(cells) == 25
            else:
                assert len(cells) == 5

    def test_all_positions_are_in_range(self):
        for cells in winning_lines(5).values():
            assert all(0 <= c < 25 for c in cells)


class TestPatternLabels:
    def test_rows_and_columns_are_one_indexed(self):
        assert pattern_label("row-0") == "Row 1"
        assert pattern_label("col-3") == "Column 4"

    def test_named_patterns(self):
        assert pattern_label("corners") == "Four Corners"
        assert pattern_label("blackout") == "BLACKOUT"
        assert "Diagonal" in pattern_label("diag-main")
