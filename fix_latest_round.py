path = "simulate_season.py"
with open(path) as f:
    content = f.read()

old = "    driver_points, team_points, _ = get_current_standings(df)"
new = "    driver_points, team_points, latest_round = get_current_standings(df)"

count = content.count(old)
print(f"Found {count} occurrence(s) of the line to fix")
assert count >= 1, "Line not found -- may already be fixed or file changed"

content = content.replace(old, new)
with open(path, "w") as f:
    f.write(content)
print("Fixed")
