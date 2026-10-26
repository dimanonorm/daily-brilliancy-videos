import re
LEGENDS = ["Carlsen", "Kasparov", "Fischer", "Morphy", "Tal", "Capablanca", "Nakamura", "Anand", "Alekhine",
           "Botvinnik", "Karpov", "Spassky", "Petrosian", "Smyslov", "Lasker", "Steinitz", "Euwe", "Kramnik",
           "Caruana", "Ding", "Gukesh", "Firouzja", "Nepomniachtchi", "Aronian", "Larsen", "Bronstein", "Keres",
           "Rubinstein", "Nimzowitsch", "Reti", "Anderssen", "Polgar", "Topalov", "Ivanchuk", "Shirov", "Short",
           "Korchnoi", "Pillsbury", "Marshall", "Zukertort", "Chigorin", "Tarrasch", "Erigaisi", "Praggnanandhaa"]
NICK = {"Nakamura": "Hikaru", "Carlsen": "Magnus", "Capablanca": "Capablanca"}

def split_name(display):
    w = display.split()
    return display, (w[-1] if w else display)

def hero_and_opp(white, black):
    def rank(n):
        for i, s in enumerate(LEGENDS):
            if re.search(r"\b" + re.escape(s) + r"\b", n): return i
        return 999
    return (white, black) if rank(white) <= rank(black) else (black, white)

TEMPLATES = [
    "{hero}'s Brilliant Move Against {opp}",
    "{short} Stuns {opp} With a Brilliant Move",
    "{hero} Finds a Brilliant Move Against {oppS}",
    "{short}'s Brilliancy Against {opp}",
    "{hero} Strikes With a Brilliant Move Against {oppS}",
]
EXTRA = ["#chessgame", "#chesstactics", "#chesshistory", "#chessclassics", "#chesspuzzle"]

def tag(s):
    return "#" + re.sub(r"[^a-z0-9]", "", s.lower())

def make(white, black, idx):
    hero, opp = hero_and_opp(white, black)
    _, heroS = split_name(hero); _, oppS = split_name(opp)
    short = NICK.get(heroS, heroS)
    if opp.lower() in ("anonymous", "amateur", "?", ""):
        title = f"{hero}'s Brilliant Move" if idx % 2 == 0 else f"{short} Finds a Brilliant Move"
    else:
        title = TEMPLATES[idx % len(TEMPLATES)].format(hero=hero, short=short, opp=opp, oppS=oppS)
    if len(title) > 100: title = f"{short}'s Brilliant Move Against {oppS}"
    herotag = tag(hero.replace(" ", ""))
    second = tag(oppS) if (idx % 2 == 0 and opp.lower() not in ("anonymous", "amateur")) else EXTRA[idx % len(EXTRA)]
    tags = ["#chess", herotag, second, "#brilliantmove", "#chesstok", "#shorts"]
    seen = []; [seen.append(t) for t in tags if t not in seen and len(t) > 1]
    return title, " ".join(seen)
