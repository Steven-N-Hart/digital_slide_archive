import events from '@girder/core/events';
import { restRequest } from '@girder/core/rest';
import HierarchyWidget from '@girder/core/views/widgets/HierarchyWidget';
import { wrap } from '@girder/core/utilities/PluginUtils';

const MODAL_ID = 'g-dicom-import-modal';
const REFRESH_MODAL_ID = 'g-dicom-refresh-modal';

/**
 * Lazily create (and cache) the import modal in the document body.
 * The modal is created once; subsequent calls just return the existing element.
 */
function getModal() {
    if ($(`#${MODAL_ID}`).length) {
        return $(`#${MODAL_ID}`);
    }

    $('body').append(`
        <div class="modal fade" id="${MODAL_ID}" tabindex="-1" role="dialog"
             aria-labelledby="g-dicom-import-title">
          <div class="modal-dialog" role="document">
            <div class="modal-content">
              <div class="modal-header">
                <button type="button" class="close" data-dismiss="modal"
                        aria-label="Close"><span aria-hidden="true">&times;</span></button>
                <h4 class="modal-title" id="g-dicom-import-title">
                  Import from DICOM store
                </h4>
              </div>
              <div class="modal-body">
                <div class="form-group">
                  <label for="g-dicom-import-urls">
                    Series URLs
                    <small class="text-muted">(one per line)</small>
                  </label>
                  <textarea id="g-dicom-import-urls" class="form-control" rows="6"
                    placeholder="https://healthcare.googleapis.com/.../studies/STUDY_UID/series/SERIES_UID">
                  </textarea>
                </div>
                <div class="form-group">
                  <label for="g-dicom-import-token">
                    GCP Bearer token
                    <small class="text-muted">
                      (optional — leave blank to use server credentials)
                    </small>
                  </label>
                  <input type="password" id="g-dicom-import-token" class="form-control"
                    placeholder="Paste output of: gcloud auth print-access-token" />
                </div>
                <div id="g-dicom-import-error" class="alert alert-danger"
                     style="display:none"></div>
              </div>
              <div class="modal-footer">
                <button type="button" class="btn btn-default"
                        data-dismiss="modal">Cancel</button>
                <button type="button" class="btn btn-primary"
                        id="g-dicom-import-submit">Import</button>
              </div>
            </div>
          </div>
        </div>`);

    $(document).on('click', '#g-dicom-import-submit', function () {
        const urls = $('#g-dicom-import-urls').val()
            .split('\n')
            .map((s) => s.trim())
            .filter(Boolean);
        const token = $('#g-dicom-import-token').val().trim() || null;
        const folderId = $(this).data('folder-id');

        if (!urls.length) {
            $('#g-dicom-import-error').text('Enter at least one series URL.').show();
            return;
        }
        $('#g-dicom-import-error').hide();
        $(this).prop('disabled', true).text('Starting\u2026');

        restRequest({
            method: 'POST',
            url: 'dicom_import/import',
            contentType: 'application/json',
            data: JSON.stringify({ urls, token, folderId }),
        }).done(() => {
            $(`#${MODAL_ID}`).modal('hide');
            events.trigger('g:alert', {
                text: 'Import job started \u2014 check the Jobs panel for progress.',
                type: 'success',
                timeout: 6000,
                icon: 'ok',
            });
        }).fail((err) => {
            const msg = (err.responseJSON && err.responseJSON.message) || err.statusText;
            $('#g-dicom-import-error').text(`Error: ${msg}`).show();
        }).always(() => {
            $('#g-dicom-import-submit').prop('disabled', false).text('Import');
        });
    });

    return $(`#${MODAL_ID}`);
}

function getRefreshModal() {
    if ($(`#${REFRESH_MODAL_ID}`).length) {
        return $(`#${REFRESH_MODAL_ID}`);
    }

    $('body').append(`
        <div class="modal fade" id="${REFRESH_MODAL_ID}" tabindex="-1" role="dialog"
             aria-labelledby="g-dicom-refresh-title">
          <div class="modal-dialog" role="document">
            <div class="modal-content">
              <div class="modal-header">
                <button type="button" class="close" data-dismiss="modal"
                        aria-label="Close"><span aria-hidden="true">&times;</span></button>
                <h4 class="modal-title" id="g-dicom-refresh-title">
                  Authorization Token Expired
                </h4>
              </div>
              <div class="modal-body">
                <p>Your DICOMweb authorization token has expired.
                   Enter a new token to restore access.</p>
                <div class="form-group">
                  <label for="g-dicom-refresh-token">New GCP Bearer token</label>
                  <input type="password" id="g-dicom-refresh-token" class="form-control"
                    placeholder="Paste output of: gcloud auth print-access-token" />
                </div>
                <div id="g-dicom-refresh-error" class="alert alert-danger"
                     style="display:none"></div>
              </div>
              <div class="modal-footer">
                <button type="button" class="btn btn-default"
                        data-dismiss="modal">Cancel</button>
                <button type="button" class="btn btn-primary"
                        id="g-dicom-refresh-submit">Refresh Token</button>
              </div>
            </div>
          </div>
        </div>`);

    $(document).on('click', '#g-dicom-refresh-submit', function () {
        const token = $('#g-dicom-refresh-token').val().trim();
        if (!token) {
            $('#g-dicom-refresh-error').text('Please enter a token.').show();
            return;
        }
        $('#g-dicom-refresh-error').hide();
        $(this).prop('disabled', true).text('Refreshing…');

        restRequest({
            method: 'POST',
            url: 'dicom_import/refresh_token',
            contentType: 'application/json',
            data: JSON.stringify({ token }),
        }).done((resp) => {
            $(`#${REFRESH_MODAL_ID}`).modal('hide');
            events.trigger('g:alert', {
                text: `Token refreshed (${resp.updated} assetstore(s) updated). ` +
                      'Reload the page to view the image.',
                type: 'success',
                timeout: 10000,
                icon: 'ok',
            });
        }).fail((err) => {
            const msg = (err.responseJSON && err.responseJSON.message) || err.statusText;
            $('#g-dicom-refresh-error').text(`Error: ${msg}`).show();
        }).always(() => {
            $('#g-dicom-refresh-submit').prop('disabled', false).text('Refresh Token');
        });
    });

    return $(`#${REFRESH_MODAL_ID}`);
}

let _refreshPending = false;

$(document).ajaxError(function (event, jqXHR, ajaxSettings) {
    if (!/\/item\/[^/]+\/tiles/.test(ajaxSettings.url || '')) return;
    const msg = ((jqXHR.responseJSON || {}).message || '').toLowerCase();
    const isAuthError = jqXHR.status === 401 ||
        (jqXHR.status === 400 && /unauthorized|401/.test(msg));
    if (isAuthError) {
        if (_refreshPending) return;
        _refreshPending = true;
        const $modal = getRefreshModal();
        $modal.find('#g-dicom-refresh-token').val('');
        $modal.find('#g-dicom-refresh-error').hide();
        $modal.find('#g-dicom-refresh-submit').prop('disabled', false).text('Refresh Token');
        $modal.one('hidden.bs.modal', () => { _refreshPending = false; });
        $modal.modal('show');
    }
});

wrap(HierarchyWidget, 'render', function (render) {
    render.call(this);

    // Only show for folder parents (not collections or user home docs)
    if (!this.parentModel || this.parentModel.resourceName !== 'folder') {
        return;
    }

    // Don't duplicate the button on re-renders
    if (this.$('.g-dicom-import-btn').length) {
        return;
    }

    const folderId = this.parentModel.id;

    const $btn = $('<button>', {
        class: 'btn btn-sm btn-default g-dicom-import-btn',
        title: 'Import series from a remote DICOMweb store into this folder',
    }).html('<i class="icon-download-cloud"></i> Import from DICOM store');

    $btn.on('click', () => {
        const $modal = getModal();
        // Reset form state each time the modal opens
        $modal.find('#g-dicom-import-urls').val('');
        $modal.find('#g-dicom-import-token').val('');
        $modal.find('#g-dicom-import-error').hide();
        $modal.find('#g-dicom-import-submit').data('folder-id', folderId);
        $modal.modal('show');
    });

    // Inject after the Upload button when present; otherwise append to the
    // action header so we appear alongside the other folder-level controls.
    const $upload = this.$('.g-upload-here-button');
    if ($upload.length) {
        $upload.after($btn);
    } else {
        this.$('.g-hierarchy-actions-header').append($btn);
    }
});
