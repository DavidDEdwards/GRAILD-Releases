// Metadata requests run in Python workers; polling keeps the desktop UI responsive.
let canaryPage = 0;
let automaticEraWatchToken = 0;
let canaryBrowserToken = 0;
let canaryInfoToken = 0;
let canaryManifestLoaded = false;
let canaryLocalFilename = null;
let canaryErasLoading = false;
let canaryErasRequested = false;
let canaryRandomToken = 0;
const knownCanaryEras = new Set(['JUTE', 'WOD', 'DRFL']);

function canaryText(tag, text, className = '') {
    const element = document.createElement(tag);
    element.textContent = text;
    element.className = className;
    return element;
}

function canaryButton(text, action) {
    const button = canaryText('button', text, 'btn btn-secondary btn-sm');
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
}

async function canaryRequest(operation, options = {}) {
    const job = await window.pywebview.api.start_canary_request(operation, options);
    if (job.status === 'failed') throw new Error(job.message);
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
        const status = await window.pywebview.api.get_canary_request(job.job_id);
        if (status.status === 'complete') return status.result;
        if (status.status === 'failed') throw new Error(status.message);
        await new Promise(resolve => window.setTimeout(resolve, 300));
    }
    throw new Error('Canary is taking longer than expected. Please try again later.');
}

function switchCatalogView(view) {
    const remote = view === 'canary';
    catalogView = view;
    syncCatalogChrome();
    document.getElementById('canary-browser').classList.toggle('hidden', !remote);
    for (const name of ['local', 'canary']) {
        const button = document.getElementById(`${name}-catalog-tab`);
        const active = name === view;
        button.setAttribute('aria-selected', String(active));
    }
    if (remote && !canaryManifestLoaded) {
        canaryManifestLoaded = true;
        loadCanaryManifest();
        searchCanary(0);
    }
}

async function loadCanaryManifest() {
    const select = document.getElementById('canary-category');
    const defaults = ['Songs', 'Remasters', 'Sessions', 'Masters', 'Freestyle'];
    function populate(categories) {
        const previous = select.value;
        select.replaceChildren();
        for (const category of ['', ...categories]) {
            const option = canaryText('option', category || 'All categories');
            option.value = category;
            select.append(option);
        }
        select.value = previous;
    }
    populate(defaults);
    try {
        const metadata = await canaryRequest('catalog');
        if (Array.isArray(metadata.categories)) populate(metadata.categories);
    } catch (error) {
        // Search remains usable if the manifest is temporarily unavailable.
        document.getElementById('canary-browser-status').textContent = error.message;
    }
}

async function searchCanary(page = 0) {
    const token = ++canaryBrowserToken;
    const status = document.getElementById('canary-browser-status');
    const button = document.getElementById('canary-search-button');
    status.textContent = 'Searching Canary...';
    button.disabled = true;
    document.getElementById('canary-previous').disabled = true;
    document.getElementById('canary-next').disabled = true;
    const loadingGrid = document.getElementById('canary-results');
    if (!loadingGrid.querySelector('article')) {
        loadingGrid.replaceChildren();
        for (let index = 0; index < 3; index += 1) loadingGrid.append(canaryText('div', '', 'skeleton h-28'));
    }
    const query = {query: document.getElementById('canary-search').value.trim(),
                   era: document.getElementById('canary-era').value.trim(),
                   category: document.getElementById('canary-category').value, page};
    try {
        const result = await canaryRequest('search', query);
        if (token !== canaryBrowserToken) return;
        canaryPage = page;
        const songs = result.songs || [];
        const grid = document.getElementById('canary-results');
        grid.replaceChildren();
        for (const song of songs) {
            if (song.era) knownCanaryEras.add(song.era);
            const card = canaryText('article', '', 'card canary-card');
            card.append(canaryText('h3', song.title, 'font-bold text-white'));
            card.append(canaryText('p', `${song.artist || 'Unknown artist'} | ${song.era || 'Era unknown'} | ${song.category || 'Uncategorized'} | ${song.duration_str || 'Duration unknown'}`, 'small'));
            card.append(canaryButton('Song information', () => openCanarySong(song.id)));
            grid.append(card);
        }
        updateCanaryEraSuggestions();
        const total = Number(result.total || 0);
        status.textContent = result._notice || `${total.toLocaleString()} matching songs. Metadata only; audio comes from your local library.`;
        if (!songs.length) grid.append(stateCard({icon: 'i-search', title: 'No matching songs', body: 'Try another title, era, or category.'}));
        document.getElementById('canary-pagination').textContent = `Page ${page + 1} of ${Math.max(1, Math.ceil(total / 20))}`;
        document.getElementById('canary-previous').disabled = page === 0;
        document.getElementById('canary-next').disabled = (page + 1) * 20 >= total;
    } catch (error) {
        if (token === canaryBrowserToken) {
            status.textContent = error.message;
            document.getElementById('canary-results').replaceChildren(stateCard({icon: 'i-cloud-off', tone: 'error',
                title: "Canary didn't respond", body: 'Your local songs and game are unaffected.',
                action: {label: 'Try again', onClick: () => searchCanary(page)}}));
        }
    } finally {
        if (token === canaryBrowserToken) button.disabled = false;
    }
}

function updateCanaryEraSuggestions() {
    const list = document.getElementById('canary-era-options');
    list.replaceChildren();
    for (const era of [...knownCanaryEras].sort()) {
        const option = document.createElement('option');
        option.value = era;
        list.append(option);
    }
}

function updateRecordingEraOptions(state) {
    const eras = [...new Set([...(state.recording_eras || []), ...(state.recording_era ? [state.recording_era] : [])])];
    for (const id of ['recording-era-select', 'hub-recording-era-select', 'account-recording-era-select']) {
        const select = document.getElementById(id);
        if (!select) continue;
        select.replaceChildren();
        for (const era of ['', ...eras]) {
            const option = canaryText('option', era || 'All eras (no era filter)');
            option.value = era;
            select.append(option);
            if (era) knownCanaryEras.add(era);
        }
        select.value = state.recording_era || '';
        select.disabled = !state.authenticated;
    }
    const eligible = state.eligible_track_count ?? state.track_count;
    const website = state.library_source === 'canary';
    for (const id of ['hub-library-source', 'account-library-source']) {
        const source = document.getElementById(id);
        if (source) { source.value = website ? 'canary' : 'local'; source.disabled = !state.authenticated; }
    }
    document.getElementById('local-game-choices')?.classList.toggle('hidden', website);
    document.getElementById('challenge-section')?.classList.toggle('hidden', website);
    document.getElementById('canary-website-choice')?.classList.toggle('hidden', !website);
    if (website && !canaryErasRequested && !canaryErasLoading) refreshCanaryEraList();
    const summary = website
        ? `Canary website shuffle: ${state.recording_era || 'all recording eras'}. Saved for your account.`
        : state.recording_era
        ? `${eligible} local song(s) in Canary era ${state.recording_era}. Saved for your account.`
        : eras.length
            ? 'All local songs are eligible. Select an era to use Canary-checked songs in that era.'
            : 'All eras selected. Canary checks your songs automatically; uncertain matches can be reviewed in Catalog.';
    for (const id of ['hub-era-summary', 'account-era-summary']) {
        const node = document.getElementById(id);
        if (node) node.textContent = summary;
    }
    updateCanaryEraSuggestions();
}

async function changeRecordingEra(era) {
    ++canaryRandomToken;
    const selects = ['recording-era-select', 'hub-recording-era-select', 'account-recording-era-select'].map(id => document.getElementById(id)).filter(Boolean);
    selects.forEach(select => { select.disabled = true; });
    try {
        const result = await window.pywebview.api.set_recording_era(era);
        renderState(result.state);
        document.getElementById('guess-input').value = '';
        document.getElementById('suggestions').classList.add('hidden');
        document.getElementById('cover-container').classList.add('hidden');
        showFeedback(result.state.error || result.message, result.success && result.state.has_track);
        for (const id of ['hub-era-summary', 'account-era-summary']) {
            const node = document.getElementById(id);
            if (node && !result.success) node.textContent = result.message;
        }
    } catch (error) {
        showFeedback('Could not change the recording era. Please try again.');
        selects.forEach(select => { select.disabled = !appState?.authenticated; });
    }
}

function addLocalCanaryControls(card, song) {
    const metadata = song.canary;
    const meta = canaryText('p', metadata
        ? `Canary: ${metadata.title} | ${metadata.era || 'Era unknown'} | ${metadata.category || 'Uncategorized'}`
        : song.canary_era ? `Canary era: ${song.canary_era} (automatically checked)`
        : song.era_status === 'needs_review' ? 'Conflicting Canary eras - review recording'
        : song.era_status === 'not_found' ? 'No exact Canary match - review recording'
        : 'Canary era check pending', 'song-meta');
    meta.dataset.status = metadata ? 'confirmed' : song.canary_era ? 'checked' : song.era_status || 'pending';
    card.append(meta);
    card.append(canaryButton(metadata ? 'Song information' : 'Match on Canary', () => openLocalCanary(song.filename)));
}

function beginCanaryInfo(filename = null) {
    canaryLocalFilename = filename;
    const dialog = document.getElementById('song-information');
    if (!dialog.open) dialog.showModal();
    document.getElementById('song-info-status').textContent = 'Loading song information...';
    document.getElementById('song-info-content').replaceChildren();
    document.getElementById('song-match-search-form').classList.toggle('hidden', !filename);
    if (filename) document.getElementById('song-match-query').value = filename.replace(/\.[^.]+$/, '');
    return ++canaryInfoToken;
}

async function openLocalCanary(filename, query) {
    const token = beginCanaryInfo(filename);
    try {
        const options = {filename};
        if (query !== undefined) options.query = query;
        const result = await canaryRequest('local', options);
        if (token !== canaryInfoToken) return;
        if (result.confirmed) renderCanarySongInfo(result, filename, true);
        else renderCanaryCandidates(result, filename);
    } catch (error) {
        if (token === canaryInfoToken) document.getElementById('song-info-status').textContent = error.message;
    }
}

function searchLocalCanary() {
    if (canaryLocalFilename) openLocalCanary(canaryLocalFilename, document.getElementById('song-match-query').value.trim());
}

function renderCanaryCandidates(result, filename) {
    const content = document.getElementById('song-info-content');
    content.replaceChildren();
    document.getElementById('song-info-status').textContent = result.notice || result.message;
    content.append(canaryText('p', `Local file: ${filename}`, 'text-sm font-bold break-words'));
    for (const song of result.candidates || []) {
        const row = canaryText('article', '', 'rounded-xl border border-slate-700 p-4 space-y-2');
        row.append(canaryText('h3', song.title, 'font-bold'));
        row.append(canaryText('p', `${song.era || 'Era unknown'} | ${song.category || 'Uncategorized'} | ${song.duration_str || 'Duration unknown'}`, 'text-xs text-slate-400'));
        row.append(canaryText('p', song.match_reason, 'text-xs text-amber-300'));
        row.append(canaryButton('Review this recording', () => openCanarySong(song.id, filename)));
        content.append(row);
    }
    if (!(result.candidates || []).length) content.append(canaryText('p', 'No candidates found. Search another title or alias below.', 'text-sm text-slate-400'));
}

async function openCanarySong(songId, filename = null) {
    const token = beginCanaryInfo(filename);
    try {
        const info = await canaryRequest('info', {song_id: songId});
        if (token === canaryInfoToken) renderCanarySongInfo(info, filename, false);
    } catch (error) {
        if (token === canaryInfoToken) document.getElementById('song-info-status').textContent = error.message;
    }
}

function renderCanarySongInfo(info, filename, confirmed) {
    const song = info.song;
    const tracker = song.tracker || {};
    const content = document.getElementById('song-info-content');
    content.replaceChildren();
    document.getElementById('song-info-status').textContent = info.notice || (confirmed ? 'Confirmed metadata for this local file.' : 'Canary song metadata');
    if (filename) content.append(canaryText('p', `Local file: ${filename}`, 'text-xs text-slate-400 break-words'));
    if (info.artwork && info.artwork.startsWith('data:image/')) {
        const image = document.createElement('img');
        image.src = info.artwork;
        image.alt = `Cover art for ${song.title}`;
        image.className = 'w-40 h-40 rounded-xl object-cover';
        content.append(image);
    }
    const fields = [['Title', song.title], ['Artist', song.artist], ['Recording era', song.era],
                    ['Category', song.category], ['Duration', song.duration_str || (song.duration_seconds ? `${song.duration_seconds} seconds` : '')],
                    ['Producer', tracker.producers], ['Recording dates', tracker.record_date],
                    ['Other dates', tracker.dates], ['Alternate names', tracker.alt_names]];
    const details = canaryText('dl', '', 'grid grid-cols-[8rem_minmax(0,1fr)] gap-x-4 gap-y-3 text-sm');
    for (const [label, value] of fields) {
        details.append(canaryText('dt', label, 'text-slate-400'));
        details.append(canaryText('dd', Array.isArray(value) ? value.join(', ') : String(value || 'Not provided'), 'whitespace-pre-line break-words text-white'));
    }
    content.append(details);
    if (song.page_url) content.append(canaryButton('Open song on Canary', async () => {
        try {
            const opened = await window.pywebview.api.open_canary_page(song.page_url);
            if (!opened.success) document.getElementById('song-info-status').textContent = 'Could not open the Canary song page.';
        }
        catch (error) { document.getElementById('song-info-status').textContent = 'Could not open your browser.'; }
    }));
    if (filename && !confirmed) {
        content.append(canaryText('p', 'Confirm only if this is the same recording. Linking metadata does not change or merge your local audio.', 'text-xs text-amber-300'));
        content.append(canaryButton('Confirm this recording', event => confirmLocalCanary(event.currentTarget, filename, song.id)));
    }
    if (filename && confirmed) content.append(canaryButton('Remove metadata match', async event => {
        event.currentTarget.disabled = true;
        try {
            const removed = await window.pywebview.api.forget_canary_match(filename);
            if (!removed.success) throw new Error(removed.message);
            await refreshCatalog();
            renderState(await window.pywebview.api.get_state());
            await openLocalCanary(filename, filename.replace(/\.[^.]+$/, ''));
        } catch (error) { document.getElementById('song-info-status').textContent = error.message; }
    }));
}

async function confirmLocalCanary(button, filename, songId) {
    const token = canaryInfoToken;
    button.disabled = true;
    document.getElementById('song-info-status').textContent = 'Saving your confirmed match...';
    try {
        const result = await canaryRequest('confirm', {filename, song_id: songId});
        await refreshCatalog();
        renderState(await window.pywebview.api.get_state());
        if (token === canaryInfoToken) renderCanarySongInfo(result, filename, true);
    } catch (error) {
        if (token === canaryInfoToken) document.getElementById('song-info-status').textContent = error.message;
        button.disabled = false;
    }
}

document.getElementById('song-information').addEventListener('close', () => { ++canaryInfoToken; });


async function changeLibrarySource(source) {
    ++canaryRandomToken;
    try {
        const result = await window.pywebview.api.set_library_source(source);
        renderState(result.state);
        if (!result.success) throw new Error(result.message);
        switchTab('hub');
    } catch (error) {
        const status = document.getElementById('hub-era-summary');
        if (status) status.textContent = error.message;
    }
}

async function refreshCanaryEraList() {
    if (canaryErasLoading) return;
    canaryErasRequested = true;
    canaryErasLoading = true;
    const status = document.getElementById('canary-era-load-status');
    const button = document.getElementById('canary-refresh-eras');
    if (button) button.disabled = true;
    if (status) status.textContent = 'Loading recording eras from Canary metadata...';
    try {
        const result = await canaryRequest('eras');
        for (const era of result.eras || []) knownCanaryEras.add(era);
        renderState(await window.pywebview.api.get_state());
        if (status) status.textContent = result._notice || 'Canary recording eras are ready.';
    } catch (error) {
        if (status) status.textContent = error.message;
    } finally {
        canaryErasLoading = false;
        if (button) button.disabled = false;
    }
}

async function playRandomCanarySong() {
    if (!appState?.authenticated) { switchTab('account'); return; }
    const token = ++canaryRandomToken;
    const button = document.getElementById('canary-random-play');
    const status = document.getElementById('canary-random-status');
    button.disabled = true;
    status.textContent = 'Choosing a random Canary song...';
    try {
        const result = await canaryRequest('random', {era: appState.recording_era || ''});
        if (token !== canaryRandomToken || !appState.authenticated || appState.library_source !== 'canary') return;
        const opened = await window.pywebview.api.open_canary_page(result.song.page_url);
        if (!opened.success) throw new Error('Could not open the Canary player in your browser.');
        status.textContent = `Opened ${result.song.title} on Canary. Press Play there to listen.`;
    } catch (error) {
        if (token === canaryRandomToken) status.textContent = error.message;
    } finally { button.disabled = false; }
}


async function watchAutomaticEraScan() {
    const token = ++automaticEraWatchToken;
    const status = document.getElementById('automatic-era-status');
    const button = document.getElementById('automatic-era-recheck');
    if (!status) return;
    try {
        while (token === automaticEraWatchToken) {
            const progress = await window.pywebview.api.get_canary_era_scan();
            if (token !== automaticEraWatchToken) return;
            const running = progress.status === 'running' || progress.status === 'queued';
            uiHook('onEraScan', progress);
            button.disabled = running;
            if (running) {
                status.textContent = `Checking Canary eras: ${progress.processed || 0} / ${progress.total || 0} songs. You can keep using the app.`;
                await new Promise(resolve => window.setTimeout(resolve, 1000));
                continue;
            }
            if (progress.status === 'complete' && progress.total) {
                status.textContent = `${progress.resolved || 0} / ${progress.total || 0} songs have Canary eras; ${progress.review || 0} need review or have no exact match.` + (progress.notice ? ` ${progress.notice}` : '');
                await refreshCatalog();
                renderState(await window.pywebview.api.get_state());
            } else if (progress.status === 'failed') {
                status.textContent = progress.message;
            } else {
                status.textContent = 'Choose a music folder to automatically check song eras on Canary.';
            }
            return;
        }
    } catch (error) {
        if (token === automaticEraWatchToken) {
            status.textContent = 'Could not check Canary eras. Local playback is still available; use Recheck to retry.';
            button.disabled = false;
        }
    }
}

async function recheckAutomaticEras() {
    try {
        await window.pywebview.api.start_canary_era_scan();
        watchAutomaticEraScan();
    } catch (error) {
        document.getElementById('automatic-era-status').textContent = 'Could not start the Canary era check. Please retry.';
    }
}
