import logging
import os
import shutil

from girder import events
from girder.plugin import GirderPlugin

logger = logging.getLogger(__name__)

_DEFAULT_ROOTS = '/Export/Shared/DSA/TRIDENT'


def _allowed_roots():
    """Return a list of realpath-normalised root prefixes (each ending in /)."""
    raw = os.environ.get('DSA_TRIDENT_CLEANUP_ROOTS') or _DEFAULT_ROOTS
    roots = []
    for r in raw.split(','):
        r = r.strip()
        if not r or not os.path.isabs(r):
            continue
        roots.append(os.path.realpath(r).rstrip(os.sep) + os.sep)
    return roots


def _is_safe_target(path, roots):
    """A path is safe to rm -rf only when:
      - it is absolute
      - the original path is not itself a symlink (prevents symlink-out attacks)
      - its realpath resolves strictly inside one of the allowed roots
      - it is an existing directory
    """
    if not path or not isinstance(path, str) or not os.path.isabs(path):
        return False
    if os.path.islink(path):
        return False
    try:
        real = os.path.realpath(path)
    except Exception:
        return False
    real_slash = real.rstrip(os.sep) + os.sep
    if not any(real_slash.startswith(r) and real_slash != r for r in roots):
        return False
    return os.path.isdir(real)


def _handle_folder_removed(event):
    folder = event.info or {}
    meta = folder.get('meta') or {}
    trident_meta = meta.get('trident') or {}
    job_dir = trident_meta.get('job_dir')
    if not job_dir:
        return

    roots = _allowed_roots()
    if not _is_safe_target(job_dir, roots):
        logger.warning(
            'trident_cleanup: refusing to remove %r (outside allowed roots %s, '
            'symlinked, or not a directory).', job_dir, roots,
        )
        return

    try:
        logger.info('trident_cleanup: removing on-disk job dir %r', job_dir)
        shutil.rmtree(job_dir)
    except Exception:
        logger.exception('trident_cleanup: failed to remove %r', job_dir)


class TridentCleanupPlugin(GirderPlugin):
    DISPLAY_NAME = 'TRIDENT Cleanup'

    def load(self, info):
        events.bind('model.folder.remove', 'trident_cleanup', _handle_folder_removed)
