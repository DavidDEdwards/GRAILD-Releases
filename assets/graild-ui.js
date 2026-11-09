window.GraildUI = (() => {
    const $ = id => document.getElementById(id);
    const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let state = null;
    let account = null;
    let unlockedBaseline = null;
    let roundLog = [];
    let roundWon = false;
    let roundHistory = [];
    let playbackFrame = 0;

    function setText(id, text) {
        const node = $(id);
        if (node) node.textContent = text;
    }

    function icon(name) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'icon');
        svg.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', `#${name}`);
        svg.append(use);
        return svg;
    }

    function make(tag, className = '', text = '') {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    let starBoost = 0;

    function buildSpace() {
        const space = make('div');
        space.id = 'space';
        space.setAttribute('aria-hidden', 'true');
        const canvas = make('canvas');
        space.append(canvas);
        document.body.prepend(space);
        const context = canvas.getContext('2d');
        let stars = [];
        let width = 0, height = 0, previous = 0;

        function resize() {
            const scale = Math.min(window.devicePixelRatio || 1, 2);
            width = window.innerWidth;
            height = window.innerHeight;
            canvas.width = width * scale;
            canvas.height = height * scale;
            context.setTransform(scale, 0, 0, scale, 0, 0);
            const count = Math.min(240, Math.round(width * height / 7500));
            stars = Array.from({length: count}, () => {
                const bright = Math.random() < .07;
                return {x: Math.random() * width, y: Math.random() * height, size: bright ? 1.2 + Math.random() * .9 : .35 + Math.random() * .7,
                        bright, lime: bright && Math.random() < .25, speed: .9 + Math.random() * 2.4, phase: Math.random() * 6.28, rate: .4 + Math.random() * 1.3};
            });
        }

        function draw(now) {
            const elapsed = Math.min(.1, (now - previous) / 1000);
            previous = now;
            context.clearRect(0, 0, width, height);
            starBoost *= .96;
            for (const star of stars) {
                star.y -= star.speed * (1 + starBoost) * elapsed;
                star.x += star.speed * .25 * (1 + starBoost) * elapsed;
                if (star.y < -6) star.y = height + 6;
                if (star.x > width + 6) star.x = -6;
                const glow = .55 + .45 * Math.sin(now / 1000 * star.rate + star.phase);
                const colour = star.lime ? '210, 245, 140' : '226, 234, 255';
                if (star.bright) {
                    context.fillStyle = `rgba(${colour}, ${.1 * glow})`;
                    context.beginPath();
                    context.arc(star.x, star.y, star.size * 3.6, 0, 6.29);
                    context.fill();
                }
                context.fillStyle = `rgba(${colour}, ${(star.bright ? .55 : .22) + (star.bright ? .45 : .4) * glow})`;
                context.beginPath();
                context.arc(star.x, star.y, star.size, 0, 6.29);
                context.fill();
            }
        }

        let last = 0;
        function loop(now) {
            window.requestAnimationFrame(loop);
            if (document.hidden || now - last < 33) return;
            last = now;
            draw(now);
        }
        window.addEventListener('resize', () => { resize(); if (reduceMotion()) draw(performance.now()); });
        resize();
        if (reduceMotion()) draw(performance.now());
        else window.requestAnimationFrame(now => { previous = now; loop(now); });
    }

    function showSongInCatalog(song) {
        switchTab('catalog');
        switchCatalogView('local');
        clearCatalogFilters();
        $('catalog-search').value = song.title;
        renderCatalog('all');
    }

    function paintHubNote() {
        const note = $('hub-note');
        if (!note || !state) return;
        const latest = roundHistory[0];
        const hasSongs = state.track_count > 0;
        note.classList.toggle('hidden', !latest && !hasSongs);
        setText('hub-note-tag', latest ? 'LAST ROUND' : 'READY');
        setText('hub-note-title', latest ? latest.title : `${state.track_count.toLocaleString()} song${state.track_count === 1 ? '' : 's'}`);
        setText('hub-note-detail', latest ? (latest.won ? `+${latest.points} pts` : 'missed') : 'in your library');
        note.onclick = () => switchTab(latest ? 'game' : 'catalog');
    }

    function toast(message, tone = '', title = '') {
        const region = $('toast-region');
        if (!region || !message) return;
        const item = make('div', `toast${tone ? ` is-${tone}` : ''}`);
        item.append(icon(tone === 'gold' ? 'i-trophy' : tone === 'bad' ? 'i-alert' : 'i-check'));
        const body = make('div');
        if (title) body.append(make('strong', '', title));
        body.append(document.createTextNode(message));
        item.append(body);
        region.append(item);
        window.setTimeout(() => {
            item.classList.add('is-out');
            window.setTimeout(() => item.remove(), 260);
        }, tone === 'gold' ? 6000 : 3800);
    }

    function confetti() {
        if (reduceMotion()) return;
        const colours = ['#5c8cff', '#c4f042', '#34d399', '#fbbf24', '#f3f5f8'];
        for (let index = 0; index < 42; index += 1) {
            const piece = make('span', 'confetti');
            piece.style.left = `${Math.random() * 100}vw`;
            piece.style.background = colours[index % colours.length];
            piece.style.animationDelay = `${Math.random() * .25}s`;
            piece.style.setProperty('--dx', `${(Math.random() - .5) * 180}px`);
            piece.style.setProperty('--rot', `${200 + Math.random() * 600}deg`);
            document.body.append(piece);
            window.setTimeout(() => piece.remove(), 2000);
        }
    }

    function paintTimeline() {
        if (!state) return;
        const durations = state.durations || [];
        document.querySelectorAll('#timeline-bars > div').forEach((bar, index) => {
            if (index >= durations.length) return;
            bar.textContent = `${durations[index]}s`;
            const logged = roundLog.find(entry => entry.at === index);
            let mode = 'future';
            if (!state.has_track) mode = 'future';
            else if (state.game_over) {
                if (roundWon && index === state.attempt) mode = 'won';
                else if (logged) mode = logged.kind;
                else mode = index <= state.attempt ? 'past' : 'future';
            } else if (index < state.attempt) mode = logged ? logged.kind : 'past';
            else if (index === state.attempt) mode = 'current';
            bar.dataset.state = mode;
        });
    }

    function paintRoundLog() {
        const list = $('round-log');
        if (!list) return;
        list.replaceChildren();
        for (const entry of roundLog) {
            const row = make('li');
            const length = (state?.durations || [])[entry.at];
            row.append(make('span', 'n', length ? `${length}s` : ''));
            row.append(make('span', 'txt', entry.kind === 'wrong' ? entry.text : 'Skipped'));
            row.append(make('span', entry.kind === 'wrong' ? 'tag-x' : 'tag-s', entry.kind === 'wrong' ? 'Wrong' : 'Skip'));
            list.append(row);
        }
        list.classList.toggle('hidden', !roundLog.length);
    }

    function paintHistory() {
        const list = $('round-history');
        if (!list) return;
        list.replaceChildren();
        for (const round of roundHistory) {
            const row = make('li', round.won ? 'is-win' : '');
            row.append(make('span', 'dot'));
            const title = make('span', 't', round.title);
            title.title = round.title;
            row.append(title);
            row.append(make('span', 'p tabular', round.won ? `+${round.points}` : 'Miss'));
            list.append(row);
        }
        $('round-history-empty')?.classList.toggle('hidden', roundHistory.length > 0);
        setText('side-rounds', roundHistory.length);
    }

    function paintBlocker() {
        const box = $('game-blocker');
        if (!box || !state) return;
        let card = null;
        if (!state.authenticated) {
            card = {icon: 'i-lock', title: 'Sign in to play', body: 'Your score, streaks and achievements save to your account.',
                    action: {label: 'Go to Account', onClick: () => switchTab('account')}};
        } else if (!state.has_track && state.track_count > 0 && state.recording_era) {
            card = {icon: 'i-disc', title: `No songs in ${state.recording_era} yet`, body: state.error || 'Canary has not matched any of your songs to this era.',
                    action: {label: 'Play all eras', onClick: () => changeRecordingEra('')}};
        } else if (!state.has_track) {
            card = {icon: 'i-folder', title: 'No songs to play', body: state.error || 'Choose a folder containing audio files.',
                    action: {label: 'Choose a music folder', onClick: () => chooseMusicFolder()}};
        }
        box.replaceChildren();
        if (card) box.append(stateCard(card));
        box.classList.toggle('hidden', !card);
        $('game-play-area')?.classList.toggle('hidden', Boolean(card));
        $('feedback-banner')?.classList.toggle('hidden', Boolean(card));
    }

    function paintGame() {
        if (!state) return;
        paintTimeline();
        paintRoundLog();
        paintBlocker();
        const over = state.game_over === true;
        $('input-area')?.classList.toggle('hidden', over);
        $('snippet-deck')?.classList.toggle('is-over', over);
        document.querySelectorAll('#snippet-eq, #play-btn, #snippet-deck .deck-meta').forEach(node => node.classList.toggle('hidden', over));
        const durations = state.durations || [];
        const worth = $('round-worth');
        if (worth) {
            const open = state.has_track && !over;
            worth.textContent = open ? `Worth ${Math.max(0, durations.length - state.attempt) * 10} pts` : '';
            worth.classList.toggle('hidden', !open);
        }
        $('game-streak-line')?.classList.toggle('is-hot', state.streak >= 3);
        const guesses = state.correct_guesses + state.wrong_guesses;
        setText('side-correct', state.correct_guesses);
        setText('side-wrong', state.wrong_guesses);
        setText('side-accuracy', guesses ? `${Math.round(state.correct_guesses * 100 / guesses)}%` : '-');
        const volume = $('volume-control');
        if (volume) volume.style.setProperty('--pct', `${volume.value}%`);
        if (over && state.title && $('cover-container')?.classList.contains('hidden')) showCover('', state.title, false);
    }

    function onPlay(duration) {
        window.cancelAnimationFrame(playbackFrame);
        const deck = $('snippet-deck');
        const progress = $('snippet-progress');
        const clock = $('snippet-clock');
        deck?.classList.add('is-playing');
        const started = performance.now();
        const tick = now => {
            const elapsed = Math.min(duration, (now - started) / 1000);
            if (progress) progress.value = duration ? elapsed * 1000 / duration : 0;
            if (clock) clock.textContent = `${elapsed.toFixed(1)}s`;
            if (elapsed < duration) playbackFrame = window.requestAnimationFrame(tick);
        };
        playbackFrame = window.requestAnimationFrame(tick);
    }

    function onPlayEnd() {
        window.cancelAnimationFrame(playbackFrame);
        $('snippet-deck')?.classList.remove('is-playing');
        const progress = $('snippet-progress');
        if (progress) progress.value = 0;
        setText('snippet-clock', '0.0s');
    }

    function onRoundResult(result, action, before) {
        const after = result.state;
        if (!before || !before.has_track || before.game_over) return;
        const guessed = action.kind === 'guess';
        if (guessed && result.success === true) {
            roundWon = true;
            confetti();
            starBoost = 40;
        } else if (guessed) {
            roundLog.push({kind: 'wrong', text: action.guess, at: before.attempt});
        } else if (action.kind === 'skip') {
            roundLog.push({kind: 'skipped', at: before.attempt});
        }
        if (after.game_over) {
            roundHistory.unshift({title: after.title || 'Unknown song', won: roundWon, points: after.score - before.score});
            roundHistory = roundHistory.slice(0, 50);
            paintHistory();
            paintHubNote();
        } else if (guessed) {
            const input = $('guess-input');
            input.value = '';
            input.focus();
            const wrap = input.closest('.guess-wrap');
            wrap?.classList.remove('shake');
            void wrap?.offsetWidth;
            wrap?.classList.add('shake');
        }
        paintGame();
    }

    function paintLibrary() {
        if (!state) return;
        const count = state.track_count;
        const hasSongs = count > 0;
        setText('library-summary', hasSongs ? `${count.toLocaleString()} audio track${count === 1 ? '' : 's'}` : (state.error || 'No songs loaded yet'));
        $('hub-library-cta')?.classList.toggle('hidden', hasSongs);
        $('hub-library-details')?.classList.toggle('hidden', !hasSongs);
        const resume = $('hub-resume');
        if (resume) {
            const open = state.authenticated && state.has_track && !state.game_over && state.attempt > 0;
            resume.classList.toggle('hidden', !open);
            resume.textContent = open ? `Resume round (attempt ${state.attempt + 1} of ${state.durations.length})` : '';
        }
    }

    function paintEraChips() {
        const box = $('hub-era-chips');
        if (!box || !state) return;
        const songs = typeof catalogSongs === 'undefined' ? [] : catalogSongs;
        const counts = new Map();
        for (const song of songs) {
            const era = song.canary?.era || song.canary_era;
            if (era) counts.set(era, (counts.get(era) || 0) + 1);
        }
        const eras = state.recording_eras || [];
        box.replaceChildren();
        box.classList.toggle('hidden', !eras.length);
        for (const era of ['', ...eras]) {
            const chip = make('button', 'chip');
            chip.type = 'button';
            chip.disabled = !state.authenticated;
            chip.setAttribute('aria-pressed', String((state.recording_era || '') === era));
            chip.append(document.createTextNode(era || 'All eras'));
            if (era && counts.has(era)) chip.append(make('span', 'count tabular', counts.get(era)));
            chip.addEventListener('click', () => changeRecordingEra(era));
            box.append(chip);
        }
    }

    function onCatalog(songs) {
        const withEra = songs.filter(song => song.canary?.era || song.canary_era).length;
        const coverage = $('hub-era-coverage');
        if (coverage) coverage.value = songs.length ? withEra * 100 / songs.length : 0;
        setText('hub-era-coverage-text', `${withEra.toLocaleString()} / ${songs.length.toLocaleString()}`);
        const formats = new Map();
        for (const song of songs) formats.set(song.format, (formats.get(song.format) || 0) + 1);
        const box = $('hub-formats');
        if (box) {
            box.replaceChildren();
            for (const [format, count] of [...formats].sort((a, b) => b[1] - a[1]).slice(0, 4)) {
                const chip = make('span', 'chip', format);
                chip.append(make('span', 'count tabular', count.toLocaleString()));
                box.append(chip);
            }
        }
        paintEraChips();
    }

    function paintAccountSummary() {
        const profile = account?.authenticated ? account.profile : null;
        setText('hub-stat-points', profile ? Number(profile.total_points).toLocaleString() : '-');
        setText('hub-stat-accuracy', profile ? `${profile.accuracy}%` : '-');
        setText('hub-stat-streak', profile ? profile.best_streak : '-');
        setText('hub-stat-played', profile ? Number(profile.songs_played).toLocaleString() : '-');
        const card = $('hub-trophy');
        if (!card) return;
        const entries = account?.achievements?.entries || [];
        const next = entries.filter(item => !item.unlocked && item.target > 0)
            .sort((a, b) => b.progress / b.target - a.progress / a.target)[0];
        card.classList.toggle('hidden', !profile || !next);
        if (!profile || !next) return;
        setText('hub-trophy-name', next.name);
        setText('hub-trophy-desc', next.description);
        const bar = $('hub-trophy-progress');
        bar.max = next.target;
        bar.value = next.progress;
        setText('hub-trophy-count', `${Number(next.progress).toLocaleString()} / ${Number(next.target).toLocaleString()}`);
    }

    function announceAchievements() {
        if (!account?.authenticated) { unlockedBaseline = null; return; }
        const entries = account.achievements?.entries || [];
        const unlocked = new Set(entries.filter(item => item.unlocked).map(item => item.id || item.name));
        if (unlockedBaseline) {
            const fresh = entries.filter(item => item.unlocked && !unlockedBaseline.has(item.id || item.name));
            fresh.slice(0, 3).forEach(item => toast(item.description, 'gold', `Trophy unlocked: ${item.name}`));
            if (fresh.length > 3) toast(`And ${fresh.length - 3} more in your trophy room.`, 'gold');
        }
        unlockedBaseline = unlocked;
    }

    function onGlobalBoard(entries) {
        const podium = $('global-podium');
        if (podium) {
            podium.replaceChildren();
            const showPodium = entries.length >= 3;
            podium.classList.toggle('hidden', !showPodium);
            if (showPodium) {
                for (const place of [1, 0, 2]) {
                    const entry = entries[place];
                    const column = make('div', `podium-col p${place + 1}`);
                    column.append(make('div', 'medal', String(entry.rank ?? place + 1)));
                    const name = make('strong', '', entry.display_name);
                    name.title = entry.display_name;
                    column.append(name);
                    column.append(make('span', 'tabular', `${Number(entry.points).toLocaleString()} pts`));
                    podium.append(column);
                }
            }
        }
        $('global-filter-wrap')?.classList.toggle('hidden', entries.length < 8);
    }

    function onSongRecords(songs) {
        const box = $('song-insights');
        if (!box) return;
        const played = songs.filter(song => song.correct + song.wrong > 0);
        box.replaceChildren();
        box.classList.toggle('hidden', !played.length);
        if (!played.length) return;
        const best = [...played].filter(song => song.correct > 0).sort((a, b) => b.correct - a.correct || a.wrong - b.wrong)[0];
        const worst = [...played].filter(song => song.wrong > 0).sort((a, b) => b.wrong - a.wrong || a.correct - b.correct)[0];
        const most = [...played].sort((a, b) => (b.correct + b.wrong) - (a.correct + a.wrong))[0];
        const cards = [
            ['Know it best', best, best ? `${best.correct} right` : ''],
            ['Most missed', worst, worst ? `${worst.wrong} wrong` : ''],
            ['Most played', most, most ? `${most.correct + most.wrong} guesses` : ''],
        ];
        for (const [label, song, detail] of cards) {
            const card = make('div', 'insight');
            card.append(make('h5', '', label));
            const title = make('p', '', song ? song.title : 'Nothing yet');
            if (song) title.title = song.title;
            card.append(title, make('small', 'tabular', detail || '-'));
            box.append(card);
        }
    }

    function onEraScan(progress) {
        const bar = $('era-scan-progress');
        if (!bar) return;
        const running = progress.status === 'running' || progress.status === 'queued';
        bar.classList.toggle('hidden', !running);
        bar.value = progress.total ? (progress.processed || 0) * 100 / progress.total : 0;
    }

    const SHORTCUTS = [
        [['Ctrl', 'K'], 'Quick search and commands'],
        [['Alt', '1-6'], 'Switch screen'],
        [['?'], 'Show this list'],
        [['Enter'], 'Game: submit your guess'],
        [['Up', 'Down'], 'Game: move through suggestions'],
        [['Ctrl', 'Space'], 'Game: play the snippet'],
        [['Space'], 'Game: play, when not typing'],
        [['Ctrl', 'Enter'], 'Game: skip / +1s'],
        [['/'], 'Game: jump to the guess box'],
        [['R'], 'Game: reroll, when not typing'],
        [['N'], 'Game: next round, when not typing'],
    ];

    function renderShortcuts(listId) {
        const list = $(listId);
        if (!list) return;
        for (const [keys, label] of SHORTCUTS) {
            const row = make('li');
            row.append(make('span', '', label));
            const group = make('span', 'keys');
            keys.forEach(key => group.append(make('kbd', '', key)));
            row.append(group);
            list.append(row);
        }
    }

    function paintUpdatePill(update) {
        const pill = $('update-status-pill');
        if (!pill) return;
        const version = update.version ? ` ${update.version}` : '';
        const labels = {
            checking: ['busy', 'Checking...'], available: ['new', `Update${version} available`],
            downloading: ['busy', 'Downloading...'], verifying: ['busy', 'Verifying...'],
            ready: ['new', 'Ready to install'], installing: ['busy', 'Installing...'],
            error: ['bad', 'Update problem'], cancelled: ['idle', 'Download cancelled'],
            current: ['good', 'Up to date'], up_to_date: ['good', 'Up to date'],
        };
        const [tone, text] = labels[update.status] || (update.status === 'idle' ? ['idle', 'Not checked yet'] : ['good', 'Up to date']);
        pill.dataset.tone = tone;
        pill.textContent = text;
    }

    function watchUpdater() {
        if (typeof window.renderUpdateState !== 'function') return;
        const original = window.renderUpdateState;
        window.renderUpdateState = update => {
            original(update);
            try { paintUpdatePill(update); } catch (error) { console.error(error); }
        };
    }

    let paletteItems = [];
    let paletteIndex = 0;

    function paletteCommands() {
        const go = (label, tab, iconName) => ({group: 'Screens', label, icon: iconName, run: () => switchTab(tab)});
        const commands = [
            go('Hub', 'hub', 'i-hub'), go('Game', 'game', 'i-game'), go('Catalog', 'catalog', 'i-catalog'),
            go('Leaderboard', 'leaderboard', 'i-board'), go('Settings', 'settings', 'i-settings'), go('Account', 'account', 'i-account'),
            {group: 'Play', label: 'Play Classic Snippets', icon: 'i-game', run: () => startGameMode('classic')},
            {group: 'Play', label: 'Play Random Drop', icon: 'i-bolt', run: () => startGameMode('random')},
        ];
        for (const [mode, rules] of Object.entries(window.GraildChallenges?.CHALLENGES || {})) {
            commands.push({group: 'Play', label: `Challenge: ${rules.name}`, icon: 'i-trophy', run: () => window.GraildChallenges.start(mode)});
        }
        if (state?.game_over) commands.push({group: 'Play', label: 'Next round', icon: 'i-game', run: () => startNextRound()});
        commands.push(
            {group: 'Actions', label: 'Choose music folder', icon: 'i-folder', run: () => chooseMusicFolder()},
            {group: 'Actions', label: 'Recheck song eras', icon: 'i-disc', run: () => { switchTab('catalog'); recheckAutomaticEras(); }},
            {group: 'Actions', label: 'View achievements', icon: 'i-trophy', run: () => { switchTab('account'); switchAccountTab('achievements'); }},
            {group: 'Actions', label: 'Keyboard shortcuts', icon: 'i-list', run: () => $('shortcuts-dialog').showModal()},
        );
        if (state?.authenticated) {
            commands.push({group: 'Eras', label: 'All eras', icon: 'i-disc', hint: 'Era filter', run: () => changeRecordingEra('')});
            for (const era of state.recording_eras || []) commands.push({group: 'Eras', label: era, icon: 'i-disc', hint: 'Era filter', run: () => changeRecordingEra(era)});
        }
        return commands;
    }

    function matches(text, terms) {
        const lower = text.toLocaleLowerCase();
        return terms.every(term => lower.includes(term));
    }

    function renderPalette() {
        const query = $('palette-input').value.trim().toLocaleLowerCase();
        const terms = query.split(/\s+/).filter(Boolean);
        let items = paletteCommands().filter(item => matches(item.label, terms));
        if (terms.length && typeof catalogSongs !== 'undefined') {
            const songs = catalogSongs.filter(song => matches(song.title, terms)).slice(0, 8);
            items = items.concat(songs.map(song => ({
                group: 'Songs in your library', label: song.title, icon: 'i-catalog', hint: 'Show in Catalog',
                run: () => showSongInCatalog(song),
            })));
        }
        paletteItems = items;
        paletteIndex = Math.min(paletteIndex, Math.max(0, items.length - 1));
        const list = $('palette-list');
        list.replaceChildren();
        if (!items.length) {
            list.append(make('p', 'palette-empty', `Nothing matches "${$('palette-input').value.trim()}".`));
            return;
        }
        let group = '';
        items.forEach((item, index) => {
            if (item.group !== group) {
                group = item.group;
                list.append(make('div', 'palette-group', group));
            }
            const row = make('button', `palette-item${index === paletteIndex ? ' is-active' : ''}`);
            row.type = 'button';
            row.setAttribute('role', 'option');
            row.setAttribute('aria-selected', String(index === paletteIndex));
            row.append(icon(item.icon), make('span', '', item.label));
            if (item.hint) row.append(make('small', '', item.hint));
            row.addEventListener('mouseenter', () => { paletteIndex = index; highlightPalette(false); });
            row.addEventListener('click', () => runPaletteItem(index));
            list.append(row);
        });
    }

    function highlightPalette(scroll = true) {
        document.querySelectorAll('#palette-list .palette-item').forEach((row, index) => {
            row.classList.toggle('is-active', index === paletteIndex);
            row.setAttribute('aria-selected', String(index === paletteIndex));
            if (scroll && index === paletteIndex) row.scrollIntoView({block: 'nearest'});
        });
    }

    function runPaletteItem(index) {
        const item = paletteItems[index];
        if (!item) return;
        $('command-palette').close();
        item.run();
    }

    function openPalette() {
        const dialog = $('command-palette');
        if (dialog.open) return;
        $('palette-input').value = '';
        paletteIndex = 0;
        renderPalette();
        dialog.showModal();
        $('palette-input').focus();
    }

    function wirePalette() {
        $('palette-open')?.addEventListener('click', openPalette);
        const input = $('palette-input');
        input.addEventListener('input', () => { paletteIndex = 0; renderPalette(); });
        input.addEventListener('keydown', event => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (!paletteItems.length) return;
                paletteIndex = (paletteIndex + (event.key === 'ArrowDown' ? 1 : paletteItems.length - 1)) % paletteItems.length;
                highlightPalette();
            } else if (event.key === 'Enter') {
                event.preventDefault();
                runPaletteItem(paletteIndex);
            }
        });
        $('command-palette').addEventListener('click', event => {
            if (event.target === event.currentTarget) event.currentTarget.close();
        });
    }

    function isTyping(target) {
        return target instanceof HTMLElement && (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable);
    }

    function pressIfEnabled(id) {
        const button = $(id);
        if (button && !button.disabled && !button.classList.contains('hidden') && button.offsetParent !== null) button.click();
    }

    function handleKeys(event) {
        if (!event.key) return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            openPalette();
            return;
        }
        if (document.querySelector('dialog[open]') || event.repeat) return;
        if (event.altKey && /^[1-6]$/.test(event.key)) {
            event.preventDefault();
            switchTab(['hub', 'game', 'catalog', 'leaderboard', 'settings', 'account'][Number(event.key) - 1]);
            return;
        }
        const typing = isTyping(event.target);
        if (!typing && event.key === '?') {
            event.preventDefault();
            $('shortcuts-dialog').showModal();
            return;
        }
        if ($('tab-game')?.classList.contains('hidden')) return;
        if (event.ctrlKey && event.code === 'Space') { event.preventDefault(); pressIfEnabled('play-btn'); return; }
        if (event.ctrlKey && event.key === 'Enter') {
            event.preventDefault();
            document.querySelector('#input-area .game-actions button:nth-child(2)')?.click();
            return;
        }
        if (typing || event.ctrlKey || event.altKey || event.metaKey) return;
        if (event.code === 'Space' && event.target?.tagName !== 'BUTTON') { event.preventDefault(); pressIfEnabled('play-btn'); }
        else if (event.key === '/') { event.preventDefault(); if (!$('guess-input').disabled) $('guess-input').focus(); }
        else if (event.key.toLowerCase() === 'r') pressIfEnabled('reroll-btn');
        else if (event.key.toLowerCase() === 'n') pressIfEnabled('next-round-btn');
    }

    function wirePasswordFields() {
        document.querySelectorAll('input[type="password"]').forEach(input => {
            const wrap = make('span', 'pw-wrap block');
            input.parentNode.insertBefore(wrap, input);
            wrap.append(input);
            const toggle = make('button', 'pw-toggle', 'Show');
            toggle.type = 'button';
            toggle.setAttribute('aria-pressed', 'false');
            toggle.setAttribute('aria-label', 'Show password');
            toggle.addEventListener('click', () => {
                const showing = input.type === 'text';
                input.type = showing ? 'password' : 'text';
                toggle.textContent = showing ? 'Show' : 'Hide';
                toggle.setAttribute('aria-pressed', String(!showing));
                toggle.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
            });
            wrap.append(toggle);
            input.form?.addEventListener('submit', () => {
                input.type = 'password';
                toggle.textContent = 'Show';
                toggle.setAttribute('aria-pressed', 'false');
            }, true);
            const capsHint = document.querySelector(`[data-caps-for="${input.id}"]`);
            if (capsHint) {
                const watch = event => capsHint.classList.toggle('hidden', !event.getModifierState?.('CapsLock'));
                input.addEventListener('keydown', watch);
                input.addEventListener('keyup', watch);
                input.addEventListener('blur', () => capsHint.classList.add('hidden'));
            }
        });
    }

    function wireRegisterHints() {
        const username = $('register-username');
        const usernameHint = $('register-username-hint');
        username?.addEventListener('input', () => {
            const value = username.value;
            const valid = /^[A-Za-z0-9_]{3,24}$/.test(value);
            usernameHint.className = `hint${!value ? '' : valid ? ' is-ok' : ' is-bad'}`;
            usernameHint.textContent = !value ? '3–24 letters, numbers, or underscores'
                : valid ? 'Looks good' : value.length < 3 ? 'Needs at least 3 characters' : 'Only letters, numbers, or underscores';
        });
        const password = $('register-password');
        const meter = $('register-password-meter');
        const passwordHint = $('register-password-hint');
        password?.addEventListener('input', () => {
            const value = password.value;
            const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter(rule => rule.test(value)).length;
            const score = !value ? 0 : value.length < 10 ? 1 : value.length >= 14 && kinds >= 3 ? 4 : value.length >= 12 && kinds >= 2 ? 3 : 2;
            meter.dataset.score = score;
            passwordHint.className = `hint${score === 1 ? ' is-bad' : score >= 2 ? ' is-ok' : ''}`;
            passwordHint.textContent = !value ? 'At least 10 characters'
                : value.length < 10 ? `${10 - value.length} more character${value.length === 9 ? '' : 's'} needed`
                : ['', '', 'Long enough', 'Good', 'Strong'][score];
        });
    }

    function wireAvatarPicker() {
        const select = $('profile-edit-avatar');
        const picker = $('avatar-picker');
        if (!select || !picker) return;
        for (const option of select.options) {
            const button = make('button', '', option.value);
            button.type = 'button';
            button.setAttribute('role', 'radio');
            button.setAttribute('aria-label', `Avatar ${option.value}`);
            button.addEventListener('click', () => { select.value = option.value; syncAvatarPicker(); });
            picker.append(button);
        }
        picker.classList.remove('hidden');
        select.classList.add('hidden');
        syncAvatarPicker();
    }

    function syncAvatarPicker() {
        const value = $('profile-edit-avatar')?.value;
        document.querySelectorAll('#avatar-picker button').forEach(button => {
            button.setAttribute('aria-checked', String(button.textContent === value));
        });
    }

    function buildWaves() {
        document.querySelectorAll('.wave').forEach(wave => {
            const middle = wave.dataset.wave === 'middle';
            for (let index = 0; index < 54; index += 1) {
                const bar = make('i');
                bar.style.height = `${22 + Math.abs(Math.sin(index * .9) * Math.cos(index * .37)) * 78}%`;
                if (middle ? index >= 25 && index < 34 : index < 9) bar.className = 'on';
                wave.append(bar);
            }
        });
        const eq = $('snippet-eq');
        if (eq) for (let index = 0; index < 26; index += 1) eq.append(make('i'));
    }

    function onState(next) {
        if (next.attempt === 0 && !next.game_over) {
            roundLog = [];
            roundWon = false;
        }
        state = next;
        paintHubNote();
        paintLibrary();
        paintEraChips();
        paintGame();
    }

    function onAccount(next) {
        account = next;
        paintAccountSummary();
        announceAchievements();
        syncAvatarPicker();
    }

    function onTab(tabId) {
        document.querySelectorAll('.rail-nav .nav-btn').forEach(button => {
            if (button.id === `btn-${tabId}`) button.setAttribute('aria-current', 'page');
            else button.removeAttribute('aria-current');
        });
    }

    function init() {
        buildSpace();
        buildWaves();
        renderShortcuts('settings-shortcuts');
        renderShortcuts('dialog-shortcuts');
        wirePalette();
        wirePasswordFields();
        wireRegisterHints();
        wireAvatarPicker();
        watchUpdater();
        document.addEventListener('keydown', handleKeys);
        $('volume-control')?.addEventListener('input', event => event.target.style.setProperty('--pct', `${event.target.value}%`));
        $('shortcuts-dialog')?.addEventListener('click', event => {
            if (event.target === event.currentTarget) event.currentTarget.close();
        });
        const more = $('catalog-more');
        if (more && 'IntersectionObserver' in window) {
            new IntersectionObserver(entries => {
                if (entries.some(entry => entry.isIntersecting) && !more.closest('.hidden')) showMoreCatalog();
            }, {rootMargin: '300px'}).observe(more);
        }
    }

    init();
    return {toast, onState, onAccount, onTab, onPlay, onPlayEnd, onRoundResult, onCatalog, onGlobalBoard, onSongRecords, onEraScan};
})();
