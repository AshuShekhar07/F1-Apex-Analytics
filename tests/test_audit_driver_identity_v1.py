from audit_driver_identity_v1 import compare


def test_compare_flags_old_number_owner_conflict_and_missing():
    # 2026 race 185 shape: #3 stored as Ricciardo (his old number), now Verstappen's
    db = [(1, 3, "Daniel Ricciardo"), (2, 16, "Charles Leclerc"), (3, 5, "Max Verstappen"), (4, 99, "Someone")]
    official = {3: "Max Verstappen", 16: "Charles  Leclerc", 5: "Gabriel Bortoleto", 44: "Lewis Hamilton"}
    status = {(c.car_number, c.status) for c in compare(185, db, official)}
    assert (3, "conflict") in status          # Verstappen already has another entry in this race
    assert (16, "ok") in status               # whitespace / case do not matter
    assert (5, "mismatch") in status
    assert (99, "unknown_number") in status and (44, "missing") in status


def test_compare_plain_mismatch():
    [c] = compare(185, [(1, 3, "Daniel Ricciardo")], {3: "Max Verstappen"})
    assert c.status == "mismatch" and c.fastf1_driver == "Max Verstappen" and c.entry_id == 1


def test_name_variant_is_not_a_mismatch():
    [c] = compare(150, [(1, 12, "Kimi Antonelli")], {12: "Andrea Kimi Antonelli"})
    assert c.status == "variant"
    [c] = compare(185, [(1, 41, "Isack Hadjar")], {41: "Arvid Lindblad"})
    assert c.status == "mismatch"
    [c] = compare(1, [(1, 47, "Mick Schumacher")], {47: "Michael Schumacher"})
    assert c.status == "mismatch"          # different first name, same surname: not the same person
