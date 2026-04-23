"""
One-time backfill: populate item['meta'] from stored item['dicomweb_meta']
for DICOM items that were imported before metadata extraction was added.

Run inside the girder container:
    docker compose exec girder bash -lc \
        'python /opt/digital_slide_archive/devops/dsa/utils/backfill_dicom_meta.py'
"""

import sys

from girder import events
from girder.models.item import Item
from girder.utility import server

# Boot Girder's model layer (no HTTP server needed)
server.configureServer()
events.daemon.start()

from girder_dicom_import.import_logic import dicom_meta_to_item_meta  # noqa: E402

query = {'dicom_uids': {'$exists': True}}
items = list(Item().find(query))
print(f'Found {len(items)} DICOM item(s) total.')

n_updated = n_skip_no_dw = n_skip_no_tags = n_already = 0

for item in items:
    if not item.get('meta'):
        pass  # needs backfill
    else:
        n_already += 1
        continue

    dw = item.get('dicomweb_meta')
    if not dw:
        print(f'  SKIP (no dicomweb_meta): {item["name"]}')
        n_skip_no_dw += 1
        continue

    item_meta = dicom_meta_to_item_meta(dw)
    if not item_meta:
        print(f'  SKIP (no recognized tags): {item["name"]}')
        n_skip_no_tags += 1
        continue

    Item().setMetadata(item, item_meta)
    print(f'  Updated ({len(item_meta)} tags): {item["name"]}')
    n_updated += 1

print()
print(f'Done. updated={n_updated}  already_had_meta={n_already}  '
      f'no_dicomweb_meta={n_skip_no_dw}  no_recognized_tags={n_skip_no_tags}')
