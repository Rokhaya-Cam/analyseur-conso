# Analyseur de consommation électrique

Outil Python qui analyse automatiquement la courbe de charge d'un bâtiment et génère un rapport d'audit énergétique.

**👉 [Voir un exemple de rapport](https://rokhaya-cam.github.io/analyseur-conso/)** (démo sur données simulées)

## Ce que fait l'outil

À partir d'un fichier CSV de mesures (date + puissance en kW), l'outil :

- calcule les indicateurs clés : énergie consommée, **talon** (puissance quand le bâtiment est vide), part de l'énergie consommée hors occupation, puissance maximale ;
- détecte les **dérives hors occupation** (ex. chauffage resté en mode confort un week-end) et chiffre leur surcoût ;
- détecte les **pics de puissance** et signale les dépassements de la puissance souscrite ;
- estime le **gain annuel** d'une réduction du talon ;
- génère des **recommandations** et un **rapport HTML** (graphiques, tableaux, hypothèses).

## Utilisation

```bash
pip install -r requirements.txt
python analyseur_conso.py
```

Pour analyser un autre bâtiment, modifier uniquement le bloc `CONFIGURATION` en haut du script : fichier, horaires d'occupation, prix du kWh, puissance souscrite.

Format attendu du CSV : 2 colonnes, date/heure puis puissance (kW), séparateur `;` ou `,`.

## Méthode

| Indicateur | Calcul |
|---|---|
| Talon | médiane de la puissance hors occupation (insensible aux anomalies) |
| Dérive | hors occupation, puissance > talon + 10 kW pendant au moins 2 h |
| Pic | puissance > 1,5 × puissance médiane en occupation |
| Gain talon | réduction de 20 % du talon sur les heures d'inoccupation, extrapolée à l'année |

Les dérives sont ponctuelles : seul le surcoût constaté est chiffré, il n'est pas extrapolé.

## Pistes d'évolution

- Test sur des données réelles (export Enedis, GTC)
- Correction climatique (DJU) et suivi Décret tertiaire
- Interface web (Streamlit)

## Auteur

Rokhaya Ndao Camara — élève ingénieure Énergie & Environnement, EIGSI La Rochelle.
Projet personnel développé avec l'aide de l'IA (Claude).
