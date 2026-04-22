import threading
import traceback

from girder.api import access
from girder.api.describe import Description, autoDescribeRoute
from girder.api.rest import Resource
from girder.constants import AccessType
from girder.models.folder import Folder
from girder.plugin import GirderPlugin

from .import_logic import (
    find_or_create_assetstore,
    get_adc_token,
    import_series,
    make_token_session,
    parse_and_deduplicate,
    refresh_assetstore_token,
    validate_single_store,
)


class DICOMImportResource(Resource):
    def __init__(self):
        super().__init__()
        self.resourceName = 'dicom_import'
        self.route('POST', ('import',), self.importSeries)
        self.route('POST', ('refresh_token',), self.refreshToken)

    @access.user
    @autoDescribeRoute(
        Description('Queue a DICOMweb series import job into the specified folder.')
        .jsonParam('body', 'JSON with urls (list), token (str, optional), folderId (str)',
                   requireObject=True, paramType='body')
        .errorResponse('No write access to target folder.', 403)
        .errorResponse('Folder not found.', 400)
    )
    def importSeries(self, body):
        from girder_jobs.models.job import Job

        user = self.getCurrentUser()
        folder_id = body['folderId']
        urls = body['urls']
        token = body.get('token') or None

        if not token:
            raise Exception('A GCP Bearer token is required. '
                            'Run `gcloud auth print-access-token` and paste the result.')

        Folder().load(folder_id, user=user, level=AccessType.WRITE, exc=True)

        job = Job().createJob(
            title='DICOMweb Import',
            type='dicom_import',
            user=user,
            public=False,
        )
        job['kwargs'] = {
            'urls': urls,
            'token': token,
            'folder_id': str(folder_id),
            'user_id': str(user['_id']),
        }
        Job().save(job)

        t = threading.Thread(target=_run_import_job, args=(job['_id'],), daemon=True)
        t.start()

        return Job().filter(job, user)

    @access.user
    @autoDescribeRoute(
        Description('Update the stored Bearer token for DICOMweb assetstores.')
        .jsonParam('body', 'JSON with token (str) and optional base_url (str)',
                   requireObject=True, paramType='body')
        .errorResponse()
    )
    def refreshToken(self, body):
        from girder.exceptions import RestException
        token = body.get('token') or None
        base_url = body.get('base_url') or None
        if not token:
            raise RestException('token is required.', code=400)
        n = refresh_assetstore_token(base_url=base_url, token=token)
        return {'updated': n}


def _run_import_job(job_id):
    from bson import ObjectId

    from girder.models.folder import Folder
    from girder.models.user import User
    from girder_jobs.constants import JobStatus
    from girder_jobs.models.job import Job

    job_model = Job()

    def _reload():
        return job_model.load(job_id, force=True)

    def _log(msg):
        job_model.updateJob(_reload(), log=msg + '\n', overwrite=False)

    job = _reload()
    try:
        job_model.updateJob(job, status=JobStatus.RUNNING)

        kwargs = _reload()['kwargs']
        urls = kwargs['urls']
        token = kwargs.get('token') or get_adc_token()
        folder_id = kwargs['folder_id']
        user_id = kwargs['user_id']

        user = User().load(ObjectId(user_id), force=True)
        folder = Folder().load(ObjectId(folder_id), force=True)

        refs, warnings = parse_and_deduplicate(urls)
        for w in warnings:
            _log(f'WARNING: {w}')

        validate_single_store(refs)
        base_url = refs[0].base_url
        _log(f'DICOMweb base URL: {base_url}')
        _log(f'Series to import: {len(refs)}')

        assetstore = find_or_create_assetstore(base_url, token)

        from dicomweb_client.api import DICOMwebClient
        session = make_token_session(token)
        client = DICOMwebClient(url=base_url, session=session)

        n_ok = n_skip = n_fail = 0
        for i, ref in enumerate(refs, 1):
            _log(f'[{i}/{len(refs)}] series={ref.series_uid}')
            try:
                _, n_inst, was_new = import_series(
                    ref, assetstore, folder, 'folder', user, client,
                )
                if was_new:
                    _log(f'  Imported ({n_inst} instances).')
                    n_ok += 1
                else:
                    _log(f'  Already present ({n_inst} instances) — updated.')
                    n_skip += 1
            except Exception:
                _log(f'  Failed:\n{traceback.format_exc()}')
                n_fail += 1

        _log(f'Done. {n_ok} imported, {n_skip} already existed, {n_fail} failed.')
        final_status = JobStatus.ERROR if n_fail else JobStatus.SUCCESS
        job_model.updateJob(_reload(), status=final_status)

    except Exception:
        job_model.updateJob(_reload(), status=JobStatus.ERROR,
                            log=traceback.format_exc(), overwrite=False)


class DICOMImportPlugin(GirderPlugin):
    DISPLAY_NAME = 'DICOMweb Import'
    CLIENT_SOURCE_PATH = 'web_client'

    def load(self, info):
        from girder.plugin import getPlugin
        getPlugin('jobs').load(info)
        info['apiRoot'].dicom_import = DICOMImportResource()
