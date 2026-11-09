let updateSettingsLoaded = false;
let updateFormError = "";
let updateDismissed = false;
let updateDownloadArmed = false;
let updateInstallStarted = false;
let updatePollPending = false;
let currentUpdateState = {status: 'idle'};

function renderUpdateState(state) {
    currentUpdateState = state;
    document.getElementById('settings-current-version').textContent = state.current_version || '0.8.0';
    const automaticOption = document.getElementById('update-automatic').closest('label');
    automaticOption.classList.toggle('hidden', state.automatic_checks_supported === false);
    if (state.automatic_checks_supported === false) {
        document.getElementById('update-settings-status').textContent = 'Automatic update checks are disabled on this Mac. Use Check for Updates to check manually.';
    }
    if (!updateSettingsLoaded) {
        document.getElementById('update-repository').value = state.repository || '';
        document.getElementById('update-channel').value = state.channel || 'beta';
        document.getElementById('update-automatic').checked = state.automatic !== false;
        updateSettingsLoaded = true;
    }
    const downloading = ['downloading', 'verifying'].includes(state.status);
    const installing = state.status === 'installing';
    document.getElementById('update-settings-status').textContent = updateFormError || (state.automatic_checks_supported === false ? 'Automatic update checks are disabled on this Mac. Use Check for Updates to check manually.' : state.message || '');
    document.getElementById('manual-update-check').disabled = downloading || installing || state.status === 'checking';
    document.getElementById('update-last-result').textContent = state.last_result ? `Last update: ${state.last_result.message || state.last_result.status}` : '';
    const failedDownload = ['error', 'cancelled'].includes(state.status) && state.version && state.version !== state.current_version;
    const visible = !updateDismissed && (failedDownload || ['available', 'downloading', 'verifying', 'ready', 'installing'].includes(state.status));
    document.getElementById('update-notification').classList.toggle('hidden', !visible);
    document.getElementById('update-version').textContent = state.version || '';
    document.getElementById('update-notes').textContent = state.notes || '';
    const percent = state.total ? Math.min(100, Math.floor((state.downloaded || 0) * 100 / state.total)) : 0;
    document.getElementById('update-status').textContent = (state.message || '') + (state.status === 'downloading' ? ` ${percent}%` : '');
    document.getElementById('update-progress').classList.toggle('hidden', !downloading);
    document.getElementById('update-progress').value = percent;
    document.getElementById('update-cancel').classList.toggle('hidden', !downloading);
    document.getElementById('update-now').disabled = downloading || installing || state.can_download === false;
    document.getElementById('update-now').textContent = state.status === 'ready' ? 'Restart & Install' : 'Update Now';
    document.getElementById('update-later').disabled = installing;
    if (state.status === 'ready' && updateDownloadArmed && !updateInstallStarted) installVerifiedUpdate();
    if (['error', 'cancelled'].includes(state.status)) updateDownloadArmed = false;
}

async function pollUpdateState() {
    if (updatePollPending) return;
    updatePollPending = true;
    try { renderUpdateState(await window.pywebview.api.get_update_state()); }
    catch (error) { document.getElementById('update-settings-status').textContent = 'Could not read update status.'; }
    finally { updatePollPending = false; }
}

async function saveUpdateSettings() {
    try {
        const result = await window.pywebview.api.save_update_settings(document.getElementById('update-repository').value.trim(), document.getElementById('update-channel').value, document.getElementById('update-automatic').checked);
        if (!result.success) throw new Error(result.message);
        updateFormError = "";
        updateSettingsLoaded = false;
        renderUpdateState(result);
    } catch (error) { updateFormError = error.message; document.getElementById('update-settings-status').textContent = error.message; }
}

async function manualUpdateCheck() {
    updateFormError = "";
    updateDismissed = false;
    updateInstallStarted = false;
    try { renderUpdateState(await window.pywebview.api.check_for_updates()); }
    catch (error) { document.getElementById('update-settings-status').textContent = 'Could not start the update check.'; }
}

async function updateNow() {
    updateDownloadArmed = true;
    updateDismissed = false;
    if (currentUpdateState.status === 'ready') { installVerifiedUpdate(); return; }
    try {
        const result = await window.pywebview.api.download_update();
        if (!result.success) throw new Error(result.message);
        renderUpdateState(result);
    } catch (error) {
        updateDownloadArmed = false;
        document.getElementById('update-status').textContent = error.message;
    }
}

async function installVerifiedUpdate() {
    updateInstallStarted = true;
    try {
        const result = await window.pywebview.api.install_update();
        if (!result.success) throw new Error(result.message);
    } catch (error) {
        updateInstallStarted = false;
        updateDownloadArmed = false;
        document.getElementById('update-status').textContent = error.message;
        document.getElementById('update-settings-status').textContent = error.message;
    }
}

async function cancelUpdateDownload() {
    updateDownloadArmed = false;
    try { renderUpdateState(await window.pywebview.api.cancel_update_download()); }
    catch (error) { document.getElementById('update-status').textContent = 'Could not cancel the download. Please retry.'; }
}

function updateLater() {
    updateDownloadArmed = false;
    updateDismissed = true;
    if (['downloading', 'verifying'].includes(currentUpdateState.status)) cancelUpdateDownload();
    document.getElementById('update-notification').classList.add('hidden');
}

window.addEventListener('pywebviewready', () => {
    pollUpdateState();
    window.setInterval(pollUpdateState, 1000);
});
