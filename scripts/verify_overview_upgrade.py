"""Offline acceptance: real product adapter, template scoring, RDKit and compositor.

The background is a deterministic test layout, not a paid image-provider result.
No project artifacts, database rows or provider credentials are touched.
"""
import argparse
import importlib.util
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont, ImageChops
from review_writer_core.stages.figures.overview_product_contract import confirmed_product_contract
from review_writer_core.stages.figures.overview_presentation import build_overview_display_contract

spec = importlib.util.spec_from_file_location('overview_acceptance', ROOT / 'skills/review-figure-style-redraw/scripts/generate_overview_figure.py')
overview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overview)


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    fixtures = {
        'confirmed-product': {'scientific_facts': [
            {'role': 'substrate', 'status': 'resolved', 'smiles': 'CC=O'},
            {'field_id': 'target_product', 'status': 'resolved', 'value': {'smiles': 'CCO'}}]},
        'substrate-only': {'representative_structure': {'role': 'substrate', 'status': 'resolved', 'smiles': 'CC=O'}},
        'invalid-smiles': {'target_product': {'status': 'resolved', 'smiles': 'not_a_smiles!'}},
    }
    catalog = overview.read_json(overview.overview_template_catalog_path())
    report = []
    for name, matrix in fixtures.items():
        contract = confirmed_product_contract(matrix=matrix)
        confirmed = contract['status'] == 'resolved'
        pack = {'module_summaries': {'S1': 'Reduction converts the carbonyl group into an alcohol.',
                                   'S2': 'Product identity requires source-backed structural confirmation.'},
                'cross_cutting': [], 'take_home': []}
        bindings = {'Transformation': {'section_id': 'S1'}, 'Evidence boundary': {'section_id': 'S2'}}
        display = build_overview_display_contract(modules=list(bindings), argument_execution={}, evidence_bindings=bindings, content_pack=pack)
        features = {'_dry_run': True, 'review_title': 'Representative reaction evidence', 'metal_categories': list(bindings),
                    'overview_modules': list(bindings), 'overview_display_contract': display,
                    'overview_structure_contract': contract, 'taxonomy_profile': 'chemistry_general',
                    '_chemistry_decision': {'mode': 'reaction' if confirmed else 'concept'}}
        template = overview.select_best_template(catalog, features)
        image = Image.new('RGB', (1200, 800), '#f3f6f7')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('DejaVuSans.ttf', 24)
        titlefont = ImageFont.truetype('DejaVuSans.ttf', 34)
        draw.rectangle((0, 0, 1199, 110), fill='#092752')
        draw.text((40, 32), 'Overview | ' + name.replace('-', ' ').title(), fill='white', font=titlefont)
        draw.text((45, 142), 'OFFLINE ACCEPTANCE / deterministic layout', fill='#506675', font=font)
        for index, module in enumerate(display['modules']):
            x = 45 + 585 * index
            draw.rounded_rectangle((x, 220, x + 525, 625), radius=18, fill='white', outline='#a8bfca', width=2)
            draw.text((x+24, 250), module['label'], fill='#164d4b', font=font)
            for item in module['items']:
                draw.multiline_text((x+24, 310), '\n'.join(textwrap.wrap(item['text'], 32)), fill='#25343d', font=font, spacing=12)
        draw.text((45, 685), 'Confirmed product: CCO' if confirmed else 'No confirmed product: no molecule or reaction drawn', fill='#164d4b', font=font)
        path = output / (name + '.png')
        image.save(path)
        title = image.crop((0, 0, 1200, 111))
        composed = False
        panel = ''
        if confirmed:
            # A disclosed synthetic fixture, not a claim about a real publication.
            scheme = {'substrate_smiles': 'CC=O', 'product_smiles': 'CCO', 'substrate_name': 'Aldehyde',
                      'product_name': 'Alcohol', 'catalyst_label': '', 'reaction_name': 'Representative reduction'}
            assert not overview._reaction_product_contract_problem(scheme, contract)
            assert overview._reaction_product_contract_problem({**scheme, 'product_smiles': 'CC=O'}, contract)
            reaction = output / 'reaction-2d.png'
            assert overview.render_reaction_scheme(scheme, reaction, style='ai3d')
            composed, reason, panel = overview.composite_skeleton_into_figure(path, reaction, template['layout_type'],
                allow_rotate=False, scheme_mode=True, allow_inserted_reaction_slot=True, reaction_slot_ratio=.14)
            assert composed, reason
        else:
            assert overview.resolve_skeleton_smiles(features) == ''
        with Image.open(path) as actual:
            unchanged = ImageChops.difference(title, actual.convert('RGB').crop((0, 0, 1200, 111))).getbbox() is None
        assert unchanged
        report.append({'case': name, 'structure_contract': contract, 'template_id': template['id'],
                       'rdkit_2d_reaction': composed, 'title_pixels_unchanged': unchanged, 'panel_source': panel,
                       'image': str(path), 'provider_called': False})
    (output / 'acceptance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    run(parser.parse_args().output)
