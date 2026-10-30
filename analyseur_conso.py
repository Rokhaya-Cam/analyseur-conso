# =====================================================================
#  ANALYSEUR DE CONSOMMATION ÉLECTRIQUE — v1
#  Auteur : Rokhaya Ndao Camara
#
#  Lit une courbe de charge (CSV), calcule les indicateurs clés,
#  détecte les anomalies, chiffre les économies et génère un rapport HTML.
#
#  Utilisation : régler la partie CONFIGURATION, puis Run (Ctrl+Shift+E).
#  Bibliothèques : pandas, matplotlib  (pip install pandas matplotlib)
# =====================================================================

import base64
import io
import webbrowser
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd


# =====================================================================
#  CONFIGURATION — la seule partie à modifier
# =====================================================================
FICHIER = "conso_bureau_janvier.csv"   # le CSV à analyser (même dossier)
NOM_SITE = "Bureau — exemple"           # nom affiché dans le rapport

# Horaires d'occupation du bâtiment
JOURS_OCCUPES = [0, 1, 2, 3, 4]         # 0 = lundi ... 6 = dimanche
HEURE_DEBUT = 7.0                       # 7h00
HEURE_FIN = 19.5                        # 19h30

# Hypothèses économiques
PRIX_KWH = 0.20                         # €/kWh (à adapter au contrat du client)
PUISSANCE_SOUSCRITE = 120               # kW — mettre None si inconnue

# Réglages de détection
MARGE_TALON = 10          # kW au-dessus du talon = dérive hors occupation
DUREE_MIN_DERIVE = 2.0    # h — une dérive plus courte est ignorée (bruit)
COEF_PIC = 1.5            # pic si puissance > 1,5 × puissance normale occupée
REDUCTION_TALON = 0.20    # scénario : on vise -20 % sur le talon
# =====================================================================


JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
BLEU, ORANGE, ROUGE, GRIS = "#2a78d6", "#eb6834", "#e34948", "#8a8984"


# ---------------------------------------------------------------------
# 1. LECTURE DES DONNÉES
# ---------------------------------------------------------------------
def lire_donnees(chemin):
    """Lit le CSV, trouve la colonne date et la colonne puissance toute seule."""
    df = pd.read_csv(chemin, sep=None, engine="python", decimal=",")
    # si le séparateur décimal était un point, la colonne est déjà numérique
    col_date = df.columns[0]
    col_p = df.columns[1]
    df = pd.DataFrame({
        "horodatage": pd.to_datetime(df[col_date], dayfirst=False),
        "puissance_kW": pd.to_numeric(
            df[col_p].astype(str).str.replace(",", ".", regex=False),
            errors="coerce"),
    })
    df = df.dropna().sort_values("horodatage").reset_index(drop=True)

    # pas de temps (en heures) = écart le plus fréquent entre deux mesures
    pas_h = df["horodatage"].diff().median().total_seconds() / 3600
    return df, pas_h


# ---------------------------------------------------------------------
# 2. INDICATEURS
# ---------------------------------------------------------------------
def calculer_indicateurs(df, pas_h):
    df["jour"] = df["horodatage"].dt.dayofweek
    df["heure"] = df["horodatage"].dt.hour + df["horodatage"].dt.minute / 60
    df["occupe"] = (df["jour"].isin(JOURS_OCCUPES)
                    & (df["heure"] >= HEURE_DEBUT)
                    & (df["heure"] < HEURE_FIN))
    df["energie_kWh"] = df["puissance_kW"] * pas_h

    inocc = df[~df["occupe"]]
    occ = df[df["occupe"]]

    talon = inocc["puissance_kW"].median()
    p_normale_occ = occ["puissance_kW"].median()
    i_max = df["puissance_kW"].idxmax()
    nb_jours = (df["horodatage"].iloc[-1] - df["horodatage"].iloc[0]).total_seconds() / 86400 + pas_h / 24

    e_tot = df["energie_kWh"].sum()
    e_inocc = inocc["energie_kWh"].sum()
    h_inocc = len(inocc) * pas_h

    return {
        "debut": df["horodatage"].iloc[0],
        "fin": df["horodatage"].iloc[-1],
        "nb_jours": nb_jours,
        "pas_min": pas_h * 60,
        "e_tot": e_tot,
        "e_inocc": e_inocc,
        "part_e_inocc": 100 * e_inocc / e_tot,
        "part_t_inocc": 100 * len(inocc) / len(df),
        "h_inocc": h_inocc,
        "talon": talon,
        "e_talon": talon * h_inocc,
        "p_normale_occ": p_normale_occ,
        "p_max": df.loc[i_max, "puissance_kW"],
        "date_p_max": df.loc[i_max, "horodatage"],
        "p_moy": df["puissance_kW"].mean(),
    }


# ---------------------------------------------------------------------
# 3. DÉTECTION DES ANOMALIES
# ---------------------------------------------------------------------
def regrouper(df, masque, pas_h):
    """Transforme des points anormaux consécutifs en 'événements'."""
    sel = df[masque].copy()
    if sel.empty:
        return []
    # nouveau groupe dès qu'il y a un trou entre deux points
    trou = sel["horodatage"].diff() > pd.Timedelta(hours=pas_h * 1.5)
    sel["groupe"] = trou.cumsum()
    evts = []
    for _, g in sel.groupby("groupe"):
        evts.append({
            "debut": g["horodatage"].iloc[0],
            "fin": g["horodatage"].iloc[-1] + pd.Timedelta(hours=pas_h),
            "duree_h": len(g) * pas_h,
            "p_moy": g["puissance_kW"].mean(),
            "p_max": g["puissance_kW"].max(),
            "data": g,
        })
    return evts


def detecter_anomalies(df, ind, pas_h):
    # A. Dérives hors occupation : bâtiment vide mais conso au-dessus du talon
    m_derive = (~df["occupe"]) & (df["puissance_kW"] > ind["talon"] + MARGE_TALON)
    derives = [e for e in regrouper(df, m_derive, pas_h) if e["duree_h"] >= DUREE_MIN_DERIVE]
    for e in derives:
        e["exces_kWh"] = ((e["data"]["puissance_kW"] - ind["talon"]) * pas_h).sum()
        e["cout"] = e["exces_kWh"] * PRIX_KWH

    # B. Pics : puissance très au-dessus du fonctionnement normal
    seuil_pic = ind["p_normale_occ"] * COEF_PIC
    m_pic = df["puissance_kW"] > seuil_pic
    pics = regrouper(df, m_pic, pas_h)
    for e in pics:
        e["depasse_souscrite"] = (PUISSANCE_SOUSCRITE is not None
                                  and e["p_max"] > PUISSANCE_SOUSCRITE)
    return derives, pics, seuil_pic


# ---------------------------------------------------------------------
# 4. ÉCONOMIES
# ---------------------------------------------------------------------
def chiffrer_economies(ind, derives):
    annualiser = 365 / ind["nb_jours"]
    gain_derives = sum(e["exces_kWh"] for e in derives)
    gain_talon = ind["talon"] * REDUCTION_TALON * ind["h_inocc"]
    # Le talon est permanent -> on l'extrapole à l'année.
    # Les dérives sont ponctuelles -> on garde seulement ce qui a été constaté.
    return {
        "derives_kWh": gain_derives,
        "derives_eur": gain_derives * PRIX_KWH,
        "talon_kWh": gain_talon,
        "an_kWh": gain_talon * annualiser,
        "an_eur": gain_talon * annualiser * PRIX_KWH,
        "part_conso": 100 * gain_talon / ind["e_tot"],
    }


# ---------------------------------------------------------------------
# 5. GRAPHIQUES
# ---------------------------------------------------------------------
def style_axes(ax):
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color("#c9c8c3")
    ax.tick_params(colors="#52514e", labelsize=9)
    ax.grid(axis="y", color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)


def fig_en_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def graphe_courbe(df, ind, derives, pics):
    fig, ax = plt.subplots(figsize=(12, 4.2))
    ax.plot(df["horodatage"], df["puissance_kW"], color=BLEU, linewidth=0.9,
            label="Puissance appelée")
    ax.axhline(ind["talon"], color=GRIS, linestyle="--", linewidth=1.2,
               label=f"Talon ({ind['talon']:.1f} kW)")
    for i, e in enumerate(derives):
        ax.axvspan(e["debut"], e["fin"], color=ORANGE, alpha=0.18,
                   label="Dérive hors occupation" if i == 0 else None)
    for i, e in enumerate(pics):
        g = e["data"]
        j = g["puissance_kW"].idxmax()
        ax.plot(g.loc[j, "horodatage"], g.loc[j, "puissance_kW"], "o",
                color=ROUGE, markersize=8, markeredgecolor="white", markeredgewidth=2,
                label="Pic anormal" if i == 0 else None)
    if PUISSANCE_SOUSCRITE:
        ax.axhline(PUISSANCE_SOUSCRITE, color=ROUGE, linestyle=":", linewidth=1,
                   label=f"Puissance souscrite ({PUISSANCE_SOUSCRITE} kW)")
    ax.set_ylabel("Puissance (kW)", color="#52514e")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    style_axes(ax)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), fontsize=8.5,
              frameon=False, ncol=5)
    return fig_en_base64(fig)


def graphe_profil(df):
    ouvre = df[df["jour"].isin(JOURS_OCCUPES)].groupby("heure")["puissance_kW"].mean()
    ferme = df[~df["jour"].isin(JOURS_OCCUPES)].groupby("heure")["puissance_kW"].mean()
    fig, ax = plt.subplots(figsize=(12, 3.6))
    ax.plot(ouvre.index, ouvre.values, color=BLEU, linewidth=2, label="Jour ouvré")
    ax.plot(ferme.index, ferme.values, color=ORANGE, linewidth=2, label="Week-end / fermé")
    ax.axvspan(HEURE_DEBUT, HEURE_FIN, color="#f1f0ec", zorder=0)
    ax.text(ouvre.index[-1] + 0.2, ouvre.values[-1], "Jour ouvré", color="#52514e",
            fontsize=9, va="center")
    ax.text(ferme.index[-1] + 0.2, ferme.values[-1], "Week-end", color="#52514e",
            fontsize=9, va="center")
    ax.set_xlim(0, 25.5)
    ax.set_xticks(range(0, 25, 2))
    ax.set_xticklabels([f"{h}h" for h in range(0, 25, 2)])
    ax.set_ylabel("Puissance moyenne (kW)", color="#52514e")
    style_axes(ax)
    ax.legend(loc="upper left", fontsize=8.5, frameon=False)
    return fig_en_base64(fig)


# ---------------------------------------------------------------------
# 6. RECOMMANDATIONS (générées à partir des résultats)
# ---------------------------------------------------------------------
def recommandations(ind, derives, pics, eco):
    reco = []
    if ind["part_e_inocc"] > 30:
        reco.append(
            f"<b>Réduire le talon ({nb(ind['talon'], 1)} kW).</b> Le bâtiment consomme "
            f"{ind['part_e_inocc']:.0f} % de son énergie quand il est vide, dont "
            f"{100 * ind['e_talon'] / ind['e_inocc']:.0f} % dus au talon. Pistes : coupure "
            f"programmée des veilles (PC, écrans, imprimantes), éclairage sur détection, "
            f"ventilation en régime réduit la nuit. Objectif -{nb(100 * REDUCTION_TALON)} % → "
            f"{nb(eco['talon_kWh'])} kWh sur la période, environ {nb(eco['an_eur'])} €/an.")
    for e in derives:
        reco.append(
            f"<b>Dérive du {fmt_date(e['debut'])} au {fmt_date(e['fin'])}</b> "
            f"({nb(e['duree_h'])} h, {nb(e['p_moy'])} kW en moyenne). Conso constante hors "
            f"occupation : vérifier la programmation horaire du CVC (mode réduit non activé ?) "
            f"et les équipements laissés en marche. Surcoût : {nb(e['cout'])} €.")
    for e in pics:
        txt = (f"<b>Pic le {fmt_date(e['debut'])}</b> ({nb(e['p_max'])} kW pendant "
               f"{nb(e['duree_h'], 1)} h). Identifier l'équipement en cause (sous-comptage, "
               f"planning de maintenance) et étaler les démarrages des gros équipements.")
        if e["depasse_souscrite"]:
            txt += " <b>Dépassement de la puissance souscrite → risque de pénalités.</b>"
        reco.append(txt)
    if not reco:
        reco.append("Aucune anomalie majeure détectée avec les réglages actuels.")
    return reco


# ---------------------------------------------------------------------
# 7. RAPPORT HTML
# ---------------------------------------------------------------------
def fmt_date(d):
    return f"{JOURS_FR[d.dayofweek]} {d:%d/%m %Hh%M}"


def fmt_heure(h):
    return f"{int(h)}h{round((h % 1) * 60):02d}"


def nb(x, dec=0):
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def generer_rapport(ind, derives, pics, seuil_pic, eco, img_courbe, img_profil, reco):
    lignes_derives = "".join(
        f"<tr><td>{fmt_date(e['debut'])}</td><td>{fmt_date(e['fin'])}</td>"
        f"<td>{nb(e['duree_h'])} h</td><td>{nb(e['p_moy'], 1)} kW</td>"
        f"<td>{nb(e['exces_kWh'])} kWh</td><td>{nb(e['cout'])} €</td></tr>"
        for e in derives) or "<tr><td colspan='6'>Aucune</td></tr>"
    lignes_pics = "".join(
        f"<tr><td>{fmt_date(e['debut'])}</td><td>{nb(e['duree_h'], 1)} h</td>"
        f"<td>{nb(e['p_max'], 1)} kW</td>"
        f"<td>{'Oui ⚠' if e['depasse_souscrite'] else 'Non'}</td></tr>"
        for e in pics) or "<tr><td colspan='4'>Aucun</td></tr>"
    li_reco = "".join(f"<li>{r}</li>" for r in reco)
    jours_occ = ", ".join(JOURS_FR[j] for j in JOURS_OCCUPES)

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rapport énergie — {NOM_SITE}</title>
<style>
  :root {{ --bg:#fcfcfb; --card:#ffffff; --ink:#0b0b0b; --ink2:#52514e; --line:#e6e5e0;
          --accent:#2a78d6; --warn:#eb6834; --crit:#e34948; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:32px 16px 64px; }}
  header {{ border-bottom:2px solid var(--ink); padding-bottom:12px; margin-bottom:24px; }}
  h1 {{ margin:0; font-size:26px; }} h2 {{ font-size:18px; margin:32px 0 12px; }}
  .meta {{ color:var(--ink2); font-size:13px; }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; }}
  .tile {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
  .tile .lab {{ color:var(--ink2); font-size:12.5px; }}
  .tile .val {{ font-size:26px; font-weight:650; margin-top:2px; }}
  .tile .sub {{ color:var(--ink2); font-size:12.5px; }}
  .tile.hl {{ border-left:4px solid var(--accent); }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; }}
  img {{ width:100%; height:auto; display:block; }}
  .tbl {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  th {{ color:var(--ink2); font-weight:600; font-size:12.5px; }}
  ul.reco li {{ margin-bottom:10px; }}
  .hyp {{ color:var(--ink2); font-size:13px; }}
  footer {{ margin-top:40px; color:var(--ink2); font-size:12px; }}
</style></head>
<body><div class="wrap">
<header>
  <h1>Analyse de consommation électrique</h1>
  <div class="meta">{NOM_SITE} · du {ind['debut']:%d/%m/%Y} au {ind['fin']:%d/%m/%Y}
   ({nb(ind['nb_jours'])} jours, pas de {nb(ind['pas_min'])} min)</div>
</header>

<h2>Synthèse</h2>
<div class="tiles">
  <div class="tile"><div class="lab">Énergie consommée</div>
    <div class="val">{nb(ind['e_tot'])} kWh</div>
    <div class="sub">{nb(ind['e_tot'] * PRIX_KWH)} € HT estimés</div></div>
  <div class="tile"><div class="lab">Talon (bâtiment vide)</div>
    <div class="val">{nb(ind['talon'], 1)} kW</div>
    <div class="sub">{nb(100 * ind['talon'] / ind['p_normale_occ'])} % de la puissance en occupation</div></div>
  <div class="tile"><div class="lab">Énergie hors occupation</div>
    <div class="val">{nb(ind['part_e_inocc'])} %</div>
    <div class="sub">pour {nb(ind['part_t_inocc'])} % du temps · {nb(ind['e_inocc'])} kWh</div></div>
  <div class="tile"><div class="lab">Puissance max</div>
    <div class="val">{nb(ind['p_max'], 1)} kW</div>
    <div class="sub">{fmt_date(ind['date_p_max'])}</div></div>
  <div class="tile hl"><div class="lab">Gain talon -{nb(100 * REDUCTION_TALON)} % / an</div>
    <div class="val">{nb(eco['an_eur'])} €</div>
    <div class="sub">{nb(eco['an_kWh'])} kWh · {nb(eco['part_conso'])} % de la conso</div></div>
  <div class="tile"><div class="lab">Surcoût des dérives constatées</div>
    <div class="val">{nb(eco['derives_eur'])} €</div>
    <div class="sub">{nb(eco['derives_kWh'])} kWh sur la période</div></div>
</div>

<h2>Courbe de charge</h2>
<div class="card"><img src="data:image/png;base64,{img_courbe}" alt="Courbe de charge avec talon, dérives et pics"></div>

<h2>Profil journalier moyen</h2>
<div class="card"><img src="data:image/png;base64,{img_profil}" alt="Profil moyen jour ouvré et week-end">
  <div class="hyp">Zone grisée : horaires d'occupation ({fmt_heure(HEURE_DEBUT)} → {fmt_heure(HEURE_FIN)}).</div></div>

<h2>Dérives hors occupation</h2>
<div class="card tbl"><table>
  <tr><th>Début</th><th>Fin</th><th>Durée</th><th>Puissance moy.</th><th>Excès / talon</th><th>Surcoût</th></tr>
  {lignes_derives}</table></div>

<h2>Pics anormaux <span class="meta">(seuil : {nb(seuil_pic)} kW)</span></h2>
<div class="card tbl"><table>
  <tr><th>Date</th><th>Durée</th><th>Puissance max</th><th>Dépasse la souscrite</th></tr>
  {lignes_pics}</table></div>

<h2>Recommandations</h2>
<div class="card"><ul class="reco">{li_reco}</ul></div>

<h2>Hypothèses</h2>
<div class="card hyp">
  Occupation : {jours_occ}, {fmt_heure(HEURE_DEBUT)} → {fmt_heure(HEURE_FIN)} ·
  Talon = médiane de la puissance hors occupation ·
  Dérive = hors occupation, &gt; talon + {MARGE_TALON} kW pendant ≥ {nb(DUREE_MIN_DERIVE)} h ·
  Pic = &gt; {nb(COEF_PIC, 1)} × puissance médiane en occupation ·
  Prix : {nb(PRIX_KWH, 2)} €/kWh HT ·
  Scénario talon : -{nb(100 * REDUCTION_TALON)} % ·
  Gain annuel du talon extrapolé depuis la période analysée (estimation : ne tient pas compte des saisons) ·
  Dérives : surcoût constaté uniquement, non extrapolé.
</div>

<footer>Rapport généré le {datetime.now():%d/%m/%Y à %H:%M} — Analyseur de consommation v1 · R. Ndao Camara</footer>
</div></body></html>"""


# ---------------------------------------------------------------------
# PROGRAMME PRINCIPAL
# ---------------------------------------------------------------------
def main():
    dossier = Path(__file__).resolve().parent
    df, pas_h = lire_donnees(dossier / FICHIER)
    ind = calculer_indicateurs(df, pas_h)
    derives, pics, seuil_pic = detecter_anomalies(df, ind, pas_h)
    eco = chiffrer_economies(ind, derives)
    img_courbe = graphe_courbe(df, ind, derives, pics)
    img_profil = graphe_profil(df)
    reco = recommandations(ind, derives, pics, eco)

    html = generer_rapport(ind, derives, pics, seuil_pic, eco, img_courbe, img_profil, reco)
    sortie = dossier / f"rapport_{Path(FICHIER).stem}.html"
    sortie.write_text(html, encoding="utf-8")

    print(f"Talon : {ind['talon']:.1f} kW | Énergie : {ind['e_tot']:.0f} kWh | "
          f"Hors occupation : {ind['part_e_inocc']:.0f} %")
    print(f"{len(derives)} dérive(s), {len(pics)} pic(s) détecté(s)")
    print(f"Gain talon : {eco['an_eur']:.0f} €/an | Surcoût dérives : {eco['derives_eur']:.0f} €")
    print(f"Rapport : {sortie}")
    webbrowser.open(sortie.as_uri())


if __name__ == "__main__":
    main()
