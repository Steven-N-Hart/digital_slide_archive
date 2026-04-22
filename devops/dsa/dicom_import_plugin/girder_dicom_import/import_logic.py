"""
Shared DICOMweb import logic used by both the CLI tool
(import_dicomweb_series.py) and the Girder plugin REST endpoint.
"""

import logging
import re
from collections import namedtuple
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

SeriesRef = namedtuple('SeriesRef', ['base_url', 'study_uid', 'series_uid'])

SOP_INSTANCE_UID_TAG = '00080018'

# Map DICOMweb hex tag → human-readable key stored in item['meta']
_DICOM_TAGS = {
    '00100010': 'PatientName',
    '00100020': 'PatientID',
    '00100030': 'PatientBirthDate',
    '00100040': 'PatientSex',
    '00080020': 'StudyDate',
    '00080030': 'StudyTime',
    '00081030': 'StudyDescription',
    '00200010': 'StudyID',
    '00200011': 'SeriesNumber',
    '0008103E': 'SeriesDescription',
    '00080060': 'Modality',
    '00180015': 'BodyPartExamined',
    '00181030': 'ProtocolName',
    '00080070': 'Manufacturer',
    '00081090': 'ManufacturerModelName',
    '00280010': 'Rows',
    '00280011': 'Columns',
    '00280030': 'PixelSpacing',
    '00080008': 'ImageType',
}


def _decode_dicom_value(tag_dict):
    """Decode a DICOMweb tag dict to a Python scalar or list."""
    vr = tag_dict.get('vr', '')
    values = tag_dict.get('Value', [])
    if not values:
        return None
    if vr == 'PN':
        decoded = [
            v.get('Alphabetic', '') if isinstance(v, dict) else str(v)
            for v in values
        ]
    else:
        decoded = list(values)
    return decoded[0] if len(decoded) == 1 else decoded


def dicom_meta_to_item_meta(dicomweb_meta):
    """Extract well-known DICOM tags into a plain dict for item['meta']."""
    result = {}
    for tag, name in _DICOM_TAGS.items():
        if tag in dicomweb_meta:
            value = _decode_dicom_value(dicomweb_meta[tag])
            if value is not None:
                result[name] = value
    return result


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

# Matches: <base>/dicomWeb/studies/<study>/series/<series>[/...]
_SERIES_RE = re.compile(
    r'^(https?://[^/]+/[^?#]*?/dicomWeb)'
    r'/studies/([^/?#]+)'
    r'/series/([^/?#]+)',
    re.IGNORECASE,
)


def _normalize_url(url):
    """Remove double slashes in the path component (common copy-paste typo)."""
    parsed = urlparse(url.strip())
    clean_path = re.sub(r'/{2,}', '/', parsed.path)
    return urlunparse(parsed._replace(path=clean_path))


def parse_series_url(url):
    """
    Parse a DICOMweb series URL into a SeriesRef.

    :raises ValueError: if the URL does not match the expected pattern.
    """
    normalized = _normalize_url(url)
    m = _SERIES_RE.match(normalized)
    if not m:
        raise ValueError(
            f'Cannot parse as a DICOMweb series URL: {url!r}\n'
            'Expected format: .../dicomWeb/studies/<STUDY_UID>/series/<SERIES_UID>'
        )
    base_url, study_uid, series_uid = m.group(1), m.group(2), m.group(3)
    return SeriesRef(base_url=base_url, study_uid=study_uid, series_uid=series_uid)


def load_urls_from_file(path):
    """Read URLs from a file or stdin ('-'). Skips blank lines and # comments."""
    import sys
    fh = sys.stdin if path == '-' else open(path)
    try:
        return [
            stripped
            for line in fh
            if (stripped := line.strip()) and not stripped.startswith('#')
        ]
    finally:
        if path != '-':
            fh.close()


def parse_and_deduplicate(raw_urls):
    """
    Parse, normalize, and deduplicate a list of raw URL strings.

    :returns: (unique_refs, warnings) — list of SeriesRef and list of warning strings.
    :raises SystemExit: on any parse failure (CLI context only).
    :raises ValueError: collects errors and raises after processing all URLs.
    """
    seen = {}
    warnings = []
    errors = []

    for raw in raw_urls:
        try:
            ref = parse_series_url(raw)
        except ValueError as e:
            errors.append(str(e))
            continue

        key = (ref.base_url, ref.study_uid, ref.series_uid)
        if key in seen:
            warnings.append(
                f'Duplicate URL (skipped): {raw!r}\n'
                f'  already seen as: {seen[key]!r}'
            )
        else:
            seen[key] = raw

    if errors:
        raise ValueError('\n'.join(errors))

    return [SeriesRef(*key) for key in seen.keys()], warnings


def validate_single_store(refs):
    """
    Verify all refs point to the same DICOMweb base URL (one assetstore).

    :raises ValueError: if multiple stores are detected.
    """
    stores = {ref.base_url for ref in refs}
    if len(stores) > 1:
        raise ValueError(
            f'URLs point to {len(stores)} different DICOMweb stores. '
            'Each store requires its own assetstore. '
            'Split your list by store and run once per store.\n  '
            + '\n  '.join(sorted(stores))
        )


# ---------------------------------------------------------------------------
# GCP authentication (Application Default Credentials)
# ---------------------------------------------------------------------------

def get_adc_token():
    """Raises — ADC is disabled. A Bearer token must be supplied explicitly."""
    raise RuntimeError(
        'A GCP Bearer token is required. '
        'Run `gcloud auth print-access-token` and pass the result via '
        '--token (CLI) or the token field in the UI.'
    )


def make_token_session(token):
    """Build a requests.Session with a static Bearer token."""
    import requests as req_lib
    session = req_lib.Session()
    session.headers['Authorization'] = f'Bearer {token}'
    return session


# ---------------------------------------------------------------------------
# Assetstore find-or-create
# ---------------------------------------------------------------------------

def _store_name_from_url(base_url):
    """Extract a human-readable store name from the DICOMweb base URL."""
    # Pattern: .../dicomStores/<name>/dicomWeb
    m = re.search(r'/dicomStores/([^/]+)/dicomWeb', base_url, re.IGNORECASE)
    return m.group(1) if m else base_url


def find_or_create_assetstore(base_url, token, dry_run=False):
    """
    Return the DICOMweb assetstore for base_url, creating it if absent.
    Always stores the provided Bearer token so Girder can serve files.

    The DICOMweb plugin must be loaded (via configureServer()) before this runs,
    so that AssetstoreType.DICOMWEB is registered.
    """
    from girder.models.assetstore import Assetstore
    from large_image_source_dicom.assetstore import DICOMWEB_META_KEY

    existing = Assetstore().findOne({f'{DICOMWEB_META_KEY}.url': base_url})
    if existing:
        logger.info('Found existing DICOMweb assetstore: %s', existing['name'])
        if not dry_run:
            existing[DICOMWEB_META_KEY]['auth_type'] = 'token'
            existing[DICOMWEB_META_KEY]['auth_token'] = token
            Assetstore().save(existing, validate=False)
        return existing

    store_name = _store_name_from_url(base_url)
    name = f'DICOMweb \u2013 {store_name}'
    logger.info('Creating DICOMweb assetstore: %s', name)
    if dry_run:
        logger.info('[dry-run] Would create assetstore %r for %s', name, base_url)
        return {'_id': None, 'name': name, DICOMWEB_META_KEY: {'url': base_url}}

    # validate=False skips the built-in connectivity check (which uses the stored
    # token before we've had a chance to set it). Token is stored so Girder can
    # authenticate when serving file downloads and tile requests.
    assetstore = Assetstore().save({
        'type': 'dicomweb',
        'name': name,
        DICOMWEB_META_KEY: {
            'url': base_url,
            'qido_prefix': None,
            'wado_prefix': None,
            'auth_type': 'token',
            'auth_token': token,
        },
    }, validate=False)
    logger.info('Assetstore created (id: %s)', assetstore['_id'])
    return assetstore


def refresh_assetstore_token(base_url, token):
    """
    Update the stored Bearer token in all DICOMweb assetstores matching base_url.
    Call this before the current token expires (~1 hour for GCP ADC tokens).
    """
    from girder.models.assetstore import Assetstore
    from large_image_source_dicom.assetstore import DICOMWEB_META_KEY

    query = {f'{DICOMWEB_META_KEY}.url': base_url} if base_url else {'type': 'dicomweb'}
    updated = 0
    for store in Assetstore().find(query):
        store[DICOMWEB_META_KEY]['auth_type'] = 'token'
        store[DICOMWEB_META_KEY]['auth_token'] = token
        Assetstore().save(store, validate=False)
        logger.info('Refreshed token for assetstore: %s', store['name'])
        updated += 1

    if updated == 0:
        logger.warning('No DICOMweb assetstores found to refresh.')
    return updated


# ---------------------------------------------------------------------------
# Instance fetch via WADO-RS
# ---------------------------------------------------------------------------

def fetch_instance_uids(client, study_uid, series_uid):
    """
    Retrieve instance UIDs for a series using WADO-RS series metadata.
    This makes exactly one HTTP call — no QIDO involved.

    :returns: list of SOPInstanceUID strings, and the raw metadata list
              (which contains rich DICOM tags usable for item metadata).
    """
    metadata = client.retrieve_series_metadata(
        study_instance_uid=study_uid,
        series_instance_uid=series_uid,
    )
    instance_uids = [
        inst[SOP_INSTANCE_UID_TAG]['Value'][0]
        for inst in metadata
        if SOP_INSTANCE_UID_TAG in inst
    ]
    return instance_uids, metadata


# ---------------------------------------------------------------------------
# Item / File creation
# ---------------------------------------------------------------------------

def import_series(ref, assetstore, dest_parent, dest_parent_type, creator_user,
                  client, dry_run=False):
    """
    Create (or reuse) a Girder Item for the series and File records for each
    instance. Mirrors DICOMwebAssetstoreAdapter._importData() but targets a
    single known series rather than the result of a QIDO search.

    :returns: (item, n_instances, was_new)
    """
    from girder.models.file import File
    from girder.models.folder import Folder
    from girder.models.item import Item

    base_url, study_uid, series_uid = ref

    logger.info('  Fetching metadata for series %s ...', series_uid)
    instance_uids, metadata = fetch_instance_uids(client, study_uid, series_uid)
    logger.info('  Found %d instances.', len(instance_uids))

    if dry_run:
        logger.info(
            '[dry-run] Would create/reuse item %s/%s with %d file(s)',
            study_uid, series_uid, len(instance_uids),
        )
        return None, len(instance_uids), True

    # Study → sub-folder under destination
    study_folder = Folder().createFolder(
        parent=dest_parent, parentType=dest_parent_type,
        name=study_uid, creator=creator_user, reuseExisting=True,
    )

    # Series → item (idempotent)
    item = Item().createItem(
        name=series_uid, creator=creator_user,
        folder=study_folder, reuseExisting=True,
    )

    was_new = 'dicom_uids' not in item

    item['dicom_uids'] = {'study_uid': study_uid, 'series_uid': series_uid}
    if metadata:
        item['dicomweb_meta'] = metadata[0]
    item = Item().save(item)

    if metadata:
        item_meta = dicom_meta_to_item_meta(metadata[0])
        if item_meta:
            Item().setMetadata(item, item_meta)
        else:
            available = sorted(metadata[0].keys())
            logger.warning(
                'No recognized DICOM tags found in series metadata. '
                'Available tags (%d): %s',
                len(available), available,
            )

    # File record per instance (idempotent via reuseExisting)
    first_file = None
    for instance_uid in instance_uids:
        f = File().createFile(
            name=f'{instance_uid}.dcm',
            creator=creator_user,
            item=item,
            reuseExisting=True,
            assetstore=assetstore,
            mimeType='application/dicom',
            size=None,
            saveFile=False,
        )
        f['dicom_uids'] = {
            'study_uid': study_uid,
            'series_uid': series_uid,
            'instance_uid': instance_uid,
        }
        f['imported'] = True
        f = File().save(f)
        if first_file is None:
            first_file = f

    # Register as a large image so HistomicsUI shows the tile viewer.
    # createJob=False skips the background verification job (not needed for
    # DICOMweb items whose source is determined at open time by the DICOM source).
    if first_file and not item.get('largeImage'):
        from girder_large_image.models.image_item import ImageItem
        ImageItem().createImageItem(item, first_file, user=creator_user, createJob=False)

    return item, len(instance_uids), was_new
