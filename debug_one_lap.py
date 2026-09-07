import fastf1
import numpy as np

fastf1.Cache.enable_cache("fastf1_cache")

session = fastf1.get_session(2024, "Silverstone", "R")
session.load(laps=True, telemetry=True, weather=False)

laps = session.laps.pick_accurate()
lap = laps.iloc[0]

print("LapStartTime:", lap["LapStartTime"])
print("LapTime:", lap["LapTime"])
print("Sector1Time:", lap["Sector1Time"])
print("Sector2Time:", lap["Sector2Time"])
print("Sector3Time:", lap["Sector3Time"])

tel = lap.get_car_data().add_distance()
print("\ntel['Time'] dtype:", tel["Time"].dtype)
print("tel['Time'] min:", tel["Time"].min())
print("tel['Time'] max:", tel["Time"].max())
print("tel['Distance'] min/max:", tel["Distance"].min(), tel["Distance"].max())

s1_end_time = lap["LapStartTime"] + lap["Sector1Time"]
s2_end_time = lap["LapStartTime"] + lap["Sector1Time"] + lap["Sector2Time"]
print("\ncomputed s1_end_time:", s1_end_time)
print("computed s2_end_time:", s2_end_time)
print("Is s1_end_time within tel Time range?", tel["Time"].min() <= s1_end_time <= tel["Time"].max())
print("Is s2_end_time within tel Time range?", tel["Time"].min() <= s2_end_time <= tel["Time"].max())
