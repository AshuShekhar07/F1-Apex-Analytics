"""The cars and liveries of 2018-2026: what timing data cannot tell.

One entry per team and season: the car's name, its power unit (maker), the livery's main
colours in words, a well-documented special livery or note where there is one, an illustrative
colour palette (for the drawing shown when there is no photo; approximate, not official
colour codes), the English Wikipedia article of the car (used by fetch_livery_photos_v1.py
to find a freely licensed photo) and, where that article's image is not right, a chosen Commons file.

Entries are matched to the database's teams by season and team name (`match`, compared without
case, spaces or punctuation). Facts only: anything not certain is left out rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Car:
    season: int
    team: str
    match: tuple[str, ...]
    car: str
    pu: str
    wiki: str
    livery: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    palette: tuple[str, ...] | None = None
    # a specific Wikimedia Commons file for the photo, when the article's own image is not a good
    # picture of this car in this livery (fetch_livery_photos_v1.py)
    photo: str | None = None

    @property
    def key(self) -> str:
        return f"{self.season}-{re.sub(r'[^a-z0-9]+', '-', self.team.lower()).strip('-')}"


# palettes (body, second colour, accent)
SILVER = ("#c4c8cc", "#1b1b1b", "#00d2be")
BLACK_MERC = ("#151515", "#c4c8cc", "#00d2be")
FERRARI = ("#d40000", "#ffffff", "#111111")
REDBULL = ("#1e2a4a", "#e3001b", "#ffcc00")
PAPAYA_BLUE = ("#ff8000", "#1e50a0", "#111111")
PAPAYA_BLACK = ("#ff8000", "#1a1a1a", "#ffffff")
RENAULT = ("#ffd800", "#111111", "#ffffff")
ALPINE = ("#0058b8", "#ffffff", "#f596c8")
PINK = ("#f596c8", "#ffffff", "#1b3d8f")
ALFA = ("#9b0000", "#ffffff", "#111111")
TORO = ("#1a2b6b", "#c0c4c8", "#e3001b")
ALPHATAURI = ("#14233d", "#ffffff", "#e3001b")
VCARB = ("#ffffff", "#1a3fa0", "#e3001b")
KICK = ("#52e252", "#111111", "#ffffff")
ASTON = ("#00594f", "#cedc00", "#ffffff")
HAAS_GREY = ("#8c8f93", "#111111", "#d40000")
HAAS_WHITE = ("#ffffff", "#111111", "#d40000")
WILLIAMS_BLUE = ("#0b2a5b", "#00a0de", "#ffffff")

MERCEDES = ("mercedes", "mercedesamgpetronasf1team", "mercedesamgpetronas")
FERRARI_N = ("ferrari", "scuderiaferrari")
REDBULL_N = ("redbullracing", "redbull", "oracleredbullracing")
MCLAREN = ("mclaren", "mclarenf1team")
HAAS = ("haasf1team", "haas", "moneygramhaasf1team")
WILLIAMS = ("williams", "williamsracing")
ALPINE_N = ("alpine", "alpinef1team", "bwtalpinef1team")
ASTON_N = ("astonmartin", "astonmartinaramcof1team", "astonmartinf1team")
RB_N = ("rb", "visacashapprb", "rbf1team", "racingbulls", "visacashappracingbulls", "vcarb")
SAUBER_N = ("kicksauber", "stakef1teamkicksauber", "sauber")

CARS: tuple[Car, ...] = (
    # --- 2018 --------------------------------------------------------------------------------
    Car(2018, "Mercedes", MERCEDES, "Mercedes AMG F1 W09 EQ Power+", "Mercedes", "Mercedes AMG F1 W09 EQ Power+",
        "Silver and black with teal", palette=SILVER),
    Car(2018, "Ferrari", FERRARI_N, "Ferrari SF71H", "Ferrari", "Ferrari SF71H", "Red with white and black", palette=FERRARI),
    Car(2018, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB14", "Renault (badged TAG Heuer)", "Red Bull Racing RB14",
        "Matte dark blue with red and yellow", palette=REDBULL),
    Car(2018, "Renault", ("renault", "renaultf1team", "renaultsportf1team"), "Renault R.S.18", "Renault", "Renault R.S.18",
        "Yellow and black", palette=RENAULT),
    Car(2018, "Haas", HAAS, "Haas VF-18", "Ferrari", "Haas VF-18", "Grey, black and red", palette=HAAS_GREY),
    Car(2018, "McLaren", MCLAREN, "McLaren MCL33", "Renault", "McLaren MCL33", "Papaya orange and blue", palette=PAPAYA_BLUE),
    Car(2018, "Force India", ("forceindia", "sahara forceindia", "forceindiaf1team"), "Force India VJM11", "Mercedes",
        "Force India VJM11", "Pink and white (BWT)", palette=PINK),
    Car(2018, "Racing Point Force India", ("racingpoint", "racingpointforceindia", "racingpointforceindiaf1team"),
        "Force India VJM11", "Mercedes", "Force India VJM11", "Pink and white (BWT)",
        ("A new entry from the Belgian Grand Prix, after the original team went into administration: its constructors' "
         "points started again from zero.",), PINK),
    Car(2018, "Sauber", ("sauber", "alfaromeosauberf1team", "alfaromeosauber"), "Sauber C37", "Ferrari", "Sauber C37",
        "Dark red and white, with Alfa Romeo branding", palette=ALFA),
    Car(2018, "Toro Rosso", ("tororosso", "scuderiatororosso", "redbulltororossohonda"), "Toro Rosso STR13", "Honda",
        "Toro Rosso STR13", "Blue and silver", palette=TORO),
    Car(2018, "Williams", WILLIAMS, "Williams FW41", "Mercedes", "Williams FW41", "White with Martini stripes",
        palette=("#ffffff", "#0b2a5b", "#e3001b")),
    # --- 2019 --------------------------------------------------------------------------------
    Car(2019, "Mercedes", MERCEDES, "Mercedes AMG F1 W10 EQ Power+", "Mercedes", "Mercedes AMG F1 W10 EQ Power+",
        "Silver and black with teal", palette=SILVER),
    Car(2019, "Ferrari", FERRARI_N, "Ferrari SF90", "Ferrari", "Ferrari SF90", "Matte red with black", palette=FERRARI),
    Car(2019, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB15", "Honda", "Red Bull Racing RB15",
        "Matte dark blue with red and yellow", ("Red Bull's first season with Honda power.",), REDBULL),
    Car(2019, "McLaren", MCLAREN, "McLaren MCL34", "Renault", "McLaren MCL34", "Papaya orange and blue", palette=PAPAYA_BLUE),
    Car(2019, "Renault", ("renault", "renaultf1team"), "Renault R.S.19", "Renault", "Renault R.S.19", "Yellow and black",
        palette=RENAULT),
    Car(2019, "Toro Rosso", ("tororosso", "scuderiatororosso", "redbulltororossohonda"), "Toro Rosso STR14", "Honda", "Toro Rosso STR14", "Blue and silver",
        palette=TORO),
    Car(2019, "Racing Point", ("racingpoint", "sportpesaracingpoint", "racingpointf1team"), "Racing Point RP19", "Mercedes",
        "Racing Point RP19", "Pink and blue", palette=PINK),
    Car(2019, "Alfa Romeo Racing", ("alfaromeoracing", "alfaromeo", "alfaromeoracingorlen"), "Alfa Romeo Racing C38",
        "Ferrari", "Alfa Romeo Racing C38", "Dark red and white", ("The Sauber team racing as Alfa Romeo.",), ALFA),
    Car(2019, "Haas", HAAS, "Haas VF-19", "Ferrari", "Haas VF-19", "Black and gold", palette=("#111111", "#c9a227", "#ffffff")),
    Car(2019, "Williams", WILLIAMS, "Williams FW42", "Mercedes", "Williams FW42", "Dark blue and white",
        palette=WILLIAMS_BLUE),
    # --- 2020 --------------------------------------------------------------------------------
    Car(2020, "Mercedes", MERCEDES, "Mercedes-AMG F1 W11 EQ Performance", "Mercedes", "Mercedes-AMG F1 W11 EQ Performance",
        "Black with silver and teal",
        ("Switched from silver to black before the first race, in support of diversity and against racism.",), BLACK_MERC),
    Car(2020, "Ferrari", FERRARI_N, "Ferrari SF1000", "Ferrari", "Ferrari SF1000", "Matte red",
        ("Raced in a burgundy livery at the Tuscan Grand Prix, Ferrari's 1000th Grand Prix.",), FERRARI),
    Car(2020, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB16", "Honda", "Red Bull Racing RB16",
        "Matte dark blue with red and yellow", palette=REDBULL),
    Car(2020, "McLaren", MCLAREN, "McLaren MCL35", "Renault", "McLaren MCL35", "Papaya orange and blue", palette=PAPAYA_BLUE),
    Car(2020, "Racing Point", ("racingpoint", "bwtracingpoint", "racingpointf1team"), "Racing Point RP20", "Mercedes",
        "Racing Point RP20", "Pink (BWT)",
        ("Nicknamed the 'pink Mercedes' for its likeness to the 2019 Mercedes; the team was fined and lost "
         "constructors' points over its brake ducts.",), PINK),
    Car(2020, "Renault", ("renault", "renaultf1team", "renaultdpworldf1team"), "Renault R.S.20", "Renault", "Renault R.S.20",
        "Yellow and black", palette=RENAULT),
    Car(2020, "AlphaTauri", ("alphatauri", "scuderiaalphatauri"), "AlphaTauri AT01", "Honda", "AlphaTauri AT01",
        "Navy blue and white", ("Toro Rosso renamed AlphaTauri.",), ALPHATAURI),
    Car(2020, "Alfa Romeo Racing", ("alfaromeoracing", "alfaromeo"), "Alfa Romeo Racing C39", "Ferrari", "Alfa Romeo Racing C39",
        "Dark red and white", palette=ALFA),
    Car(2020, "Haas", HAAS, "Haas VF-20", "Ferrari", "Haas VF-20", "Grey, black and red", palette=HAAS_GREY),
    Car(2020, "Williams", WILLIAMS, "Williams FW43", "Mercedes", "Williams FW43", "Dark blue and white", palette=WILLIAMS_BLUE),
    # --- 2021 --------------------------------------------------------------------------------
    Car(2021, "Mercedes", MERCEDES, "Mercedes-AMG F1 W12 E Performance", "Mercedes", "Mercedes-AMG F1 W12 E Performance",
        "Black with teal and silver", palette=BLACK_MERC),
    Car(2021, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB16B", "Honda", "Red Bull Racing RB16B",
        "Matte dark blue with red and yellow",
        ("A white livery at the Turkish Grand Prix, a thank-you to Honda in its last season as works partner.",), REDBULL),
    Car(2021, "Ferrari", FERRARI_N, "Ferrari SF21", "Ferrari", "Ferrari SF21", "Red with a burgundy fade", palette=FERRARI),
    Car(2021, "McLaren", MCLAREN, "McLaren MCL35M", "Mercedes", "McLaren MCL35M", "Papaya orange and blue",
        ("Raced in Gulf Oil blue and orange at the Monaco Grand Prix.",), PAPAYA_BLUE),
    Car(2021, "Alpine", ALPINE_N, "Alpine A521", "Renault", "Alpine A521", "Blue, white and red",
        ("Renault renamed Alpine.",), ALPINE),
    Car(2021, "AlphaTauri", ("alphatauri", "scuderiaalphatauri"), "AlphaTauri AT02", "Honda", "AlphaTauri AT02",
        "Navy blue and white", palette=ALPHATAURI),
    Car(2021, "Aston Martin", ASTON_N, "Aston Martin AMR21", "Mercedes", "Aston Martin AMR21", "British racing green",
        ("Aston Martin's return as a works team (formerly Racing Point).",), ASTON),
    Car(2021, "Williams", WILLIAMS, "Williams FW43B", "Mercedes", "Williams FW43B", "Dark blue and white", palette=WILLIAMS_BLUE,
        photo="search:FW43B|Latifi 2021|Russell 2021|Williams 2021"),
    Car(2021, "Alfa Romeo Racing", ("alfaromeoracing", "alfaromeo"), "Alfa Romeo Racing C41", "Ferrari", "Alfa Romeo Racing C41",
        "Dark red and white", palette=ALFA),
    Car(2021, "Haas", HAAS, "Haas VF-21", "Ferrari", "Haas VF-21", "White, blue and red",
        palette=("#ffffff", "#1b3d8f", "#d40000")),
    # --- 2022 --------------------------------------------------------------------------------
    Car(2022, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB18", "Red Bull Powertrains (Honda)", "Red Bull Racing RB18",
        "Matte dark blue with red and yellow", ("The first season of the ground-effect rules.",), REDBULL),
    Car(2022, "Ferrari", FERRARI_N, "Ferrari F1-75", "Ferrari", "Ferrari F1-75", "Red and black",
        ("Modena yellow touches at the Italian Grand Prix for Ferrari's 75th anniversary.",), ("#d40000", "#111111", "#ffd700")),
    Car(2022, "Mercedes", MERCEDES, "Mercedes-AMG F1 W13 E Performance", "Mercedes", "Mercedes-AMG F1 W13 E Performance",
        "Silver with black and teal", ("Silver returned after two black seasons.",), SILVER),
    Car(2022, "Alpine", ALPINE_N, "Alpine A522", "Renault", "Alpine A522", "Blue and pink", palette=ALPINE),
    Car(2022, "McLaren", MCLAREN, "McLaren MCL36", "Mercedes", "McLaren MCL36", "Papaya orange and blue", palette=PAPAYA_BLUE),
    Car(2022, "Alfa Romeo", ("alfaromeo", "alfaromeof1team", "alfaromeof1teamorlen"), "Alfa Romeo C42", "Ferrari",
        "Alfa Romeo C42", "Dark red, white and black", palette=ALFA),
    Car(2022, "Aston Martin", ASTON_N, "Aston Martin AMR22", "Mercedes", "Aston Martin AMR22", "British racing green with lime",
        palette=ASTON),
    Car(2022, "Haas", HAAS, "Haas VF-22", "Ferrari", "Haas VF-22", "White, black and red",
        ("The colours of its former Russian title sponsor were removed before the season.",), HAAS_WHITE),
    Car(2022, "AlphaTauri", ("alphatauri", "scuderiaalphatauri"), "AlphaTauri AT03", "Red Bull Powertrains (Honda)",
        "AlphaTauri AT03", "Navy blue and white", palette=ALPHATAURI),
    Car(2022, "Williams", WILLIAMS, "Williams FW44", "Mercedes", "Williams FW44", "Dark blue", palette=WILLIAMS_BLUE),
    # --- 2023 --------------------------------------------------------------------------------
    Car(2023, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB19", "Honda RBPT", "Red Bull Racing RB19",
        "Matte dark blue with red and yellow",
        ("Won 21 of the 22 Grands Prix: the highest share of wins of any car in the championship's history.",), REDBULL),
    Car(2023, "Mercedes", MERCEDES, "Mercedes-AMG F1 W14 E Performance", "Mercedes", "Mercedes-AMG F1 W14 E Performance",
        "Black, much of it bare carbon to save weight", palette=BLACK_MERC),
    Car(2023, "Ferrari", FERRARI_N, "Ferrari SF-23", "Ferrari", "Ferrari SF-23", "Red with black", palette=FERRARI),
    Car(2023, "McLaren", MCLAREN, "McLaren MCL60", "Mercedes", "McLaren MCL60", "Papaya orange, blue and black",
        ("Named for McLaren's 60th anniversary; a chrome livery at the British Grand Prix.",), PAPAYA_BLUE),
    Car(2023, "Aston Martin", ASTON_N, "Aston Martin AMR23", "Mercedes", "Aston Martin AMR23", "British racing green with lime",
        palette=ASTON),
    Car(2023, "Alpine", ALPINE_N, "Alpine A523", "Renault", "Alpine A523", "Blue and pink", palette=ALPINE),
    Car(2023, "Williams", WILLIAMS, "Williams FW45", "Mercedes", "Williams FW45", "Dark blue", palette=WILLIAMS_BLUE),
    Car(2023, "AlphaTauri", ("alphatauri", "scuderiaalphatauri"), "AlphaTauri AT04", "Honda RBPT", "AlphaTauri AT04",
        "Navy blue and white", palette=ALPHATAURI),
    Car(2023, "Alfa Romeo", ("alfaromeo", "alfaromeof1team", "alfaromeof1teamstake"), "Alfa Romeo C43", "Ferrari",
        "Alfa Romeo C43", "Dark red and black", palette=ALFA),
    Car(2023, "Haas", HAAS, "Haas VF-23", "Ferrari", "Haas VF-23", "Black, white and red", palette=HAAS_WHITE),
    # --- 2024 --------------------------------------------------------------------------------
    Car(2024, "McLaren", MCLAREN, "McLaren MCL38", "Mercedes", "McLaren MCL38", "Papaya orange and anthracite",
        ("Constructors' champions, McLaren's first title since 1998.",
         "An Ayrton Senna tribute livery at the Monaco Grand Prix."), PAPAYA_BLACK),
    Car(2024, "Ferrari", FERRARI_N, "Ferrari SF-24", "Ferrari", "Ferrari SF-24", "Red with white and yellow",
        ("A livery with light and dark blue at the Miami Grand Prix.",), FERRARI),
    Car(2024, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB20", "Honda RBPT", "Red Bull Racing RB20",
        "Matte dark blue with red and yellow", palette=REDBULL),
    Car(2024, "Mercedes", MERCEDES, "Mercedes-AMG F1 W15 E Performance", "Mercedes", "Mercedes-AMG F1 W15 E Performance",
        "Black and silver", palette=BLACK_MERC),
    Car(2024, "Aston Martin", ASTON_N, "Aston Martin AMR24", "Mercedes", "Aston Martin AMR24", "British racing green with lime",
        palette=ASTON),
    Car(2024, "RB", RB_N, "RB VCARB 01", "Honda RBPT", "RB VCARB 01", "White and blue",
        ("AlphaTauri renamed RB.",), VCARB),
    Car(2024, "Haas", HAAS, "Haas VF-24", "Ferrari", "Haas VF-24", "White, black and red", palette=HAAS_WHITE),
    Car(2024, "Williams", WILLIAMS, "Williams FW46", "Mercedes", "Williams FW46", "Dark and light blue", palette=WILLIAMS_BLUE),
    Car(2024, "Alpine", ALPINE_N, "Alpine A524", "Renault", "Alpine A524", "Blue and pink", palette=ALPINE),
    Car(2024, "Kick Sauber", SAUBER_N, "Kick Sauber C44", "Ferrari", "Kick Sauber C44", "Fluorescent green and black",
        palette=KICK),
    # --- 2025 --------------------------------------------------------------------------------
    Car(2025, "McLaren", MCLAREN, "McLaren MCL39", "Mercedes", "McLaren MCL39", "Papaya orange and black",
        ("Constructors' champions for the second year running.",), PAPAYA_BLACK),
    Car(2025, "Ferrari", FERRARI_N, "Ferrari SF-25", "Ferrari", "Ferrari SF-25", "Red with black and white", palette=FERRARI),
    Car(2025, "Mercedes", MERCEDES, "Mercedes-AMG F1 W16 E Performance", "Mercedes", "Mercedes-AMG F1 W16 E Performance",
        "Black and silver", palette=BLACK_MERC),
    Car(2025, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB21", "Honda RBPT", "Red Bull Racing RB21",
        "Matte dark blue with red and yellow", ("A white livery at the Japanese Grand Prix, honouring Honda.",), REDBULL),
    Car(2025, "Williams", WILLIAMS, "Williams FW47", "Mercedes", "Williams FW47", "Dark blue", palette=WILLIAMS_BLUE),
    Car(2025, "Racing Bulls", RB_N, "Racing Bulls VCARB 02", "Honda RBPT", "Racing Bulls VCARB 02", "White and blue",
        palette=VCARB),
    Car(2025, "Aston Martin", ASTON_N, "Aston Martin AMR25", "Mercedes", "Aston Martin AMR25", "British racing green with lime",
        palette=ASTON),
    Car(2025, "Haas", HAAS, "Haas VF-25", "Ferrari", "Haas VF-25", "White, black and red", palette=HAAS_WHITE),
    Car(2025, "Kick Sauber", SAUBER_N, "Kick Sauber C45", "Ferrari", "Kick Sauber C45", "Fluorescent green and black",
        palette=KICK),
    Car(2025, "Alpine", ALPINE_N, "Alpine A525", "Renault", "Alpine A525", "Blue and pink", palette=ALPINE),
    # --- 2026: new rules (smaller cars, active aerodynamics, about half the power electric) ---
    Car(2026, "Mercedes", MERCEDES, "Mercedes-AMG F1 W17", "Mercedes", "Mercedes-AMG F1 W17"),
    Car(2026, "Ferrari", FERRARI_N, "Ferrari SF-26", "Ferrari", "Ferrari SF-26"),
    Car(2026, "McLaren", MCLAREN, "McLaren MCL40", "Mercedes", "McLaren MCL40"),
    Car(2026, "Red Bull Racing", REDBULL_N, "Red Bull Racing RB22", "Red Bull Ford", "Red Bull Racing RB22",
        notes=("The first season with Red Bull's own power unit, built with Ford.",)),
    Car(2026, "Aston Martin", ASTON_N, "Aston Martin AMR26", "Honda", "Aston Martin AMR26",
        notes=("Honda's works partner from 2026.",)),
    Car(2026, "Alpine", ALPINE_N, "Alpine A526", "Mercedes", "Alpine A526",
        notes=("Alpine's first season as a customer team, with Mercedes power.",)),
    Car(2026, "Williams", WILLIAMS, "Williams FW48", "Mercedes", "Williams FW48"),
    Car(2026, "Racing Bulls", RB_N, "Racing Bulls VCARB 03", "Red Bull Ford", "Racing Bulls VCARB 03"),
    Car(2026, "Haas", HAAS, "Haas VF-26", "Ferrari", "Haas VF-26"),
    Car(2026, "Audi", ("audi", "audif1team", "audirevolutf1team", "sauber", "kicksauber"), "Audi R26", "Audi", "Audi R26",
        notes=("Audi's first season, taking over the Sauber team.",)),
    Car(2026, "Cadillac", ("cadillac", "cadillacf1team", "cadillacformula1team"), "Cadillac MAC-26", "Ferrari",
        "Cadillac MAC-26", notes=("The team's first season. MAC-26 stands for Mario Andretti Cadillac, after the 1978 "
                                  "world champion.",)),
)


def normalise(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


_BY_SEASON: dict[int, list[Car]] = {}
for _c in CARS:
    _BY_SEASON.setdefault(_c.season, []).append(_c)


def find(season: int, team_name: str | None) -> Car | None:
    """The catalogue entry for a database team in a season, if there is one."""
    n = normalise(team_name)
    for car in _BY_SEASON.get(season, ()):
        if n in {normalise(m) for m in car.match} or n == normalise(car.team):
            return car
    return None
