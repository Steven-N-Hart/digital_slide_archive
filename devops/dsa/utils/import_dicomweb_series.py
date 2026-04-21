#!/usr/bin/env python3
"""
import_dicomweb_series.py — Import specific DICOMweb series into DSA by URL.

Accepts a list of DICOMweb series URLs (from args, a file, or stdin), finds or
creates a DICOMweb assetstore for the backing store, fetches instance UIDs via
WADO-RS metadata (one call per series — no QIDO scan), and writes Girder
Item/File records directly to MongoDB.

This is designed to import a curated subset of series from a store that may
contain millions of slides, where a full QIDO-based import would be impractical.

Usage (run inside the girder container):

  # Positional URL arguments
  python import_dicomweb_series.py URL [URL ...]

  # From a file (one URL per line; blank lines and lines starting with # ignored)
  python import_dicomweb_series.py --file series_list.txt

  # From stdin
  cat series_list.txt | python import_dicomweb_series.py --file -

  # Dry-run (parse and validate only, no writes)
  python import_dicomweb_series.py --dry-run URL [URL ...]

  # Specify destination folder (Girder resource path)
  python import_dicomweb_series.py --folder "collection/Pathology/WSIs" --file list.txt

Example URL format (Google Cloud Healthcare API):
  https://healthcare.googleapis.com/v1/projects/PROJECT/locations/LOCATION/
      datasets/DATASET/dicomStores/STORE/dicomWeb/studies/STUDY_UID/series/SERIES_UID
"""

import argparse
import logging
import sys
import traceback

# Core import logic lives in the installed plugin package so it is shared with
# the Girder web-UI endpoint without duplication.
from girder_dicom_import.import_logic import (
    find_or_create_assetstore,
    import_series,
    load_urls_from_file,
    make_token_session,
    parse_and_deduplicate,
    refresh_assetstore_token,
    validate_single_store,
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Girder bootstrap
# ---------------------------------------------------------------------------

def bootstrap_girder():
    """
    Initialize the Girder environment (load plugins, connect to MongoDB).
    Mirrors the pattern in provision.py.
    """
    from girder import _attachFileLogHandlers
    from girder.utility.server import configureServer

    _attachFileLogHandlers()
    configureServer()


# ---------------------------------------------------------------------------
# Destination folder resolution
# ---------------------------------------------------------------------------

def resolve_destination(folder_path, admin_user, creator_user):
    """
    Resolve a Girder resource path to a folder document, creating any
    intermediate folders as needed.

    Supported path formats:

      collection/<CollectionName>[/<sub> ...]
          Top-level collection, created if absent (requires admin).

      user/<login>/Public[/<sub> ...]
      user/<login>/Private[/<sub> ...]
          Folder inside a user's Public or Private home directory.
          The user must already exist.  Sub-folders are created as needed.

    :param folder_path: slash-separated Girder path.
    :param admin_user: admin used for privileged operations (collection create).
    :param creator_user: set as creator of any new sub-folders.
    :returns: (parent document, parent type string)
    """
    from girder.models.collection import Collection
    from girder.models.folder import Folder
    from girder.models.user import User

    parts = [p for p in folder_path.split('/') if p]
    if len(parts) < 2:
        logger.error(
            '--folder must have at least two segments. '
            'Examples: "collection/MyCollection", '
            '"user/m087494/Public/Project 1". Got: %r', folder_path,
        )
        sys.exit(1)

    root_type = parts[0].lower()

    if root_type == 'collection':
        root_name = parts[1]
        coll = Collection().findOne({'name': root_name})
        if coll is None:
            logger.info('Creating collection: %s', root_name)
            coll = Collection().createCollection(
                name=root_name, creator=admin_user, public=True,
            )
        parent = coll
        parent_type = 'collection'
        sub_parts = parts[2:]

    elif root_type == 'user':
        if len(parts) < 3:
            logger.error(
                'user/ paths must include the login and Public or Private, '
                'e.g. "user/m087494/Public/Project 1". Got: %r', folder_path,
            )
            sys.exit(1)
        login = parts[1]
        home_folder_name = parts[2]  # 'Public' or 'Private'
        if home_folder_name not in ('Public', 'Private'):
            logger.error(
                'Second segment of a user/ path must be "Public" or "Private". '
                'Got: %r', home_folder_name,
            )
            sys.exit(1)
        target_user = User().findOne({'login': login})
        if target_user is None:
            logger.error('User %r not found. Create the user first.', login)
            sys.exit(1)
        home_folder = Folder().findOne({
            'parentId': target_user['_id'],
            'parentCollection': 'user',
            'name': home_folder_name,
        })
        if home_folder is None:
            logger.error(
                '%s folder not found for user %r.', home_folder_name, login,
            )
            sys.exit(1)
        parent = home_folder
        parent_type = 'folder'
        sub_parts = parts[3:]

    else:
        logger.error(
            '--folder path must start with "collection/" or "user/". Got: %r',
            folder_path,
        )
        sys.exit(1)

    for segment in sub_parts:
        parent = Folder().createFolder(
            parent=parent, parentType=parent_type,
            name=segment, creator=creator_user, reuseExisting=True,
        )
        parent_type = 'folder'

    return parent, parent_type


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'urls', nargs='*', metavar='URL',
        help='One or more DICOMweb series URLs.',
    )
    parser.add_argument(
        '--file', '-f', metavar='PATH',
        help='File of URLs (one per line). Use "-" for stdin.',
    )
    parser.add_argument(
        '--folder', default='collection/DICOMweb Imports',
        metavar='GIRDER_PATH',
        help=(
            'Destination as a Girder resource path, e.g. '
            '"collection/Pathology/WSIs". '
            'Default: "collection/DICOMweb Imports".'
        ),
    )
    parser.add_argument(
        '--username', default=None, metavar='LOGIN',
        help='Girder admin username to act as (default: first admin found).',
    )
    parser.add_argument(
        '--token', default=None, metavar='BEARER_TOKEN', required=True,
        help='GCP Bearer token. Run `gcloud auth print-access-token` to obtain one.',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Parse and validate only; do not write to the database.',
    )
    parser.add_argument(
        '--refresh-token', action='store_true',
        help=(
            'Refresh the stored Bearer token in all DICOMweb assetstores '
            'and exit. Run this before the current token expires (~55 min). '
            'Does not require any URL arguments.'
        ),
    )
    args = parser.parse_args()

    # Token-refresh mode: no URLs needed
    if args.refresh_token:
        if not args.token:
            parser.error('--token is required for --refresh-token.')
        bootstrap_girder()
        n = refresh_assetstore_token(base_url=None, token=args.token)
        logger.info('Done. %d assetstore(s) updated.', n)
        return

    # Collect raw URLs
    raw_urls = list(args.urls)
    if args.file:
        raw_urls.extend(load_urls_from_file(args.file))
    if not raw_urls:
        parser.error('Provide at least one URL (as an argument or via --file).')

    # Parse, deduplicate, validate
    try:
        refs, warnings = parse_and_deduplicate(raw_urls)
    except ValueError as e:
        for line in str(e).splitlines():
            logger.error(line)
        sys.exit(1)

    for w in warnings:
        logger.warning(w)

    try:
        validate_single_store(refs)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    base_url = refs[0].base_url
    logger.info('DICOMweb base URL: %s', base_url)
    logger.info('Series to import: %d', len(refs))

    if args.dry_run:
        logger.info('[dry-run] Parsed refs:')
        for ref in refs:
            logger.info('  study=%s  series=%s', ref.study_uid, ref.series_uid)
        logger.info('[dry-run] Done — no writes performed.')
        return

    # Bootstrap Girder (loads plugins, connects to MongoDB)
    logger.info('Bootstrapping Girder environment ...')
    bootstrap_girder()

    from girder.models.user import User

    # Admin user — needed for privileged operations (assetstore, collection create).
    # Always resolved independently of --username.
    admin_user = User().findOne({'admin': True})
    if admin_user is None:
        logger.error('No admin user found in the database.')
        sys.exit(1)

    # Creator user — owns the imported resources.  Defaults to admin.
    # If --username is given and the user does not exist, it is created
    # with a temporary password that is printed to stdout.
    if args.username:
        creator_user = User().findOne({'login': args.username})
        if creator_user is None:
            import secrets
            tmp_password = secrets.token_urlsafe(16)
            creator_user = User().createUser(
                login=args.username,
                password=tmp_password,
                firstName=args.username,
                lastName=args.username,
                email=f'{args.username}@localhost.local',
                admin=False,
                public=False,
            )
            logger.info(
                'Created user %r with temporary password: %s  '
                '(change this via the Girder UI or API)',
                args.username, tmp_password,
            )
        else:
            logger.info('Found existing user: %s', creator_user['login'])
    else:
        creator_user = admin_user

    logger.info('Resources will be owned by: %s', creator_user['login'])

    token = args.token

    assetstore = find_or_create_assetstore(base_url, token)

    # Resolve destination folder
    dest_parent, dest_parent_type = resolve_destination(args.folder, admin_user, creator_user)
    logger.info('Destination: %s (%s)', dest_parent.get('name'), dest_parent_type)

    session = make_token_session(token)

    from dicomweb_client.api import DICOMwebClient
    client = DICOMwebClient(url=base_url, session=session)

    # Import each series
    n_imported = 0
    n_skipped = 0
    n_failed = 0

    for i, ref in enumerate(refs, 1):
        logger.info('[%d/%d] series=%s', i, len(refs), ref.series_uid)
        try:
            _, n_instances, was_new = import_series(
                ref, assetstore, dest_parent, dest_parent_type,
                creator_user, client,
            )
            if was_new:
                logger.info('  Imported (%d instances).', n_instances)
                n_imported += 1
            else:
                logger.info('  Already present (%d instances) — updated.', n_instances)
                n_skipped += 1
        except Exception:
            logger.error('  Failed:\n%s', traceback.format_exc())
            n_failed += 1

    logger.info(
        'Done. %d imported, %d already existed (updated), %d failed.',
        n_imported, n_skipped, n_failed,
    )
    if n_failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
