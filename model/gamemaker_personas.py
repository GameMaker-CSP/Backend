"""UESL content for the existing OCS persona API, without relabeling old answers."""
import json
from pathlib import Path
from __init__ import db
from model.persona import Persona

def init_gamemaker_personas():
    catalogue = json.loads((Path(__file__).resolve().parents[1] / 'data/gamemaker_personas.json').read_text())
    for item in catalogue:
        if Persona.query.filter_by(_alias=item['alias']).first() is None:
            db.session.add(Persona(
                _alias=item['alias'], _category=item['category'],
                _bio_map={'title': item['title'], 'description': item['description'], 'archetype': []},
            ))
    db.session.commit()
