from researchscout.taxonomy import (
    all_groups,
    archive_of,
    archives_for,
    archives_for_group,
    group_for,
)


def test_archive_of_splits_on_the_first_dot() -> None:
    assert archive_of("cs.LG") == "cs"
    assert archive_of("astro-ph.CO") == "astro-ph"
    assert archive_of("gr-qc") == "gr-qc"
    assert archive_of("physics.optics") == "physics"


def test_tech_and_non_tech_partition_every_archive() -> None:
    tech = archives_for("tech")
    non_tech = archives_for("non_tech")
    assert tech & non_tech == frozenset()
    covered = {archive for group in all_groups() for archive in archives_for_group(group.key)}
    assert tech | non_tech == covered


def test_tech_archives_are_computer_science_broadly() -> None:
    assert archives_for("tech") == frozenset({"cs", "stat", "eess"})


def test_physics_family_shares_one_group() -> None:
    for category in ("hep-th", "quant-ph", "cond-mat.str-el", "physics.optics"):
        group = group_for(category)
        assert group is not None
        assert group.key == "physics"
        assert group.tech is False


def test_group_for_maps_and_rejects() -> None:
    cs = group_for("cs.LG")
    assert cs is not None and cs.key == "cs" and cs.tech is True
    assert group_for(None) is None
    assert group_for("notreal.XX") is None


def test_archives_for_group() -> None:
    assert archives_for_group("cs") == frozenset({"cs"})
    assert "hep-lat" in archives_for_group("physics")
    assert archives_for_group("notreal") == frozenset()


def test_group_labels_and_keys_are_unique() -> None:
    groups = all_groups()
    assert len({group.key for group in groups}) == len(groups)
    assert len({group.label for group in groups}) == len(groups)
