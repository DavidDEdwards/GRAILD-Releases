window.GraildChallenges = (() => {
    const $ = id => document.getElementById(id);
    const api = () => window.pywebview.api;

    const CHALLENGES = {
        sudden_death: {
            name: 'Sudden Death', unit: 'songs',
            blurb: 'One wrong guess ends the run. Skipping is allowed, so listen longer if you need to.',
        },
        three_lives: {
            name: 'Three Lives', unit: 'songs', lives: 3,
            blurb: 'Miss a song and you lose a life. See how far three lives take you.',
        },
        time_attack: {
            name: 'Time Attack', unit: 'pts', seconds: 90,
            blurb: '90 seconds on the clock. New songs load and play by themselves, so keep guessing.',
        },
        sprint: {
            name: 'Sprint', unit: 'pts', songs: 10,
            blurb: 'Ten songs. Score as many points as you can. Fewer attempts means more points.',
        },
    };

    let run = null;
    let lastRun = null;
    let bests = {};
    let clock = 0;
    let advanceTimer = 0;
    let finishing = false;

    const score = r => (CHALLENGES[r.mode].unit === 'songs' ? r.solved : r.points);
    const unitText = (mode, value) => (CHALLENGES[mode].unit === 'songs'
        ? `${value} song${value === 1 ? '' : 's'}` : `${Number(value).toLocaleString()} pts`);

    function make(tag, className = '', text = '') {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== '') node.textContent = text;
        return node;
    }

    function toast(...args) {
        window.GraildUI?.toast?.(...args);
    }

    async function start(mode) {
        if (!CHALLENGES[mode]) return;
        if (typeof appState === 'undefined' || !appState?.authenticated) {
            switchTab('account');
            $('auth-message').textContent = 'Sign in to play a challenge.';
            return;
        }
        if (!appState.track_count) {
            toast('Choose a music folder with songs first.', 'bad');
            switchTab('game');
            return;
        }
        if (run) await finish('left');
        window.clearTimeout(advanceTimer);
        lastRun = null;
        paintSummary();
        const rules = CHALLENGES[mode];
        run = {mode, solved: 0, played: 0, points: 0, lives: rules.lives || 0, endsAt: 0, wrongGuess: false};
        switchTab('game');
        await startNextRound();
        if (!run || run.mode !== mode) return;
        if (rules.seconds) {
            run.endsAt = Date.now() + rules.seconds * 1000;
            clock = window.setInterval(tickClock, 250);
            autoPlay();
        }
        paintAll();
    }

    async function finish(reason) {
        if (!run || finishing) return;
        finishing = true;
        window.clearInterval(clock);
        window.clearTimeout(advanceTimer);
        const ended = {...run, reason, newBest: false, saveError: ''};
        run = null;
        try {
            if (ended.played > 0 || reason !== 'left') {
                const result = await api().save_challenge_result(ended.mode, score(ended), ended.solved);
                if (result.success) {
                    bests = result.bests || bests;
                    ended.newBest = result.new_best === true;
                } else {
                    ended.saveError = result.message || 'This run could not be saved.';
                }
            }
        } catch (error) {
            ended.saveError = 'This run could not be saved.';
        } finally {
            finishing = false;
        }
        if (reason !== 'left') {
            lastRun = ended;
            if (ended.newBest) toast(`${unitText(ended.mode, score(ended))} in ${CHALLENGES[ended.mode].name}.`, 'gold', 'New personal best!');
        }
        paintAll();
        if (typeof appState !== 'undefined' && appState) renderState(appState);
    }

    function tickClock() {
        if (!run?.endsAt) return;
        if (Date.now() >= run.endsAt) {
            finish('time');
            return;
        }
        paintHud();
    }

    function autoPlay() {
        if (!run || typeof appState === 'undefined' || !appState?.has_track || appState.game_over) return;
        if (!$('tab-game').classList.contains('hidden')) playSnippetSimulation();
    }

    async function endSongAfterWrongGuess() {
        roundActionPending = true;
        try {
            let result = null;
            for (let step = 0; step < 12; step += 1) {
                result = await api().skip_attempt();
                if (result.state.game_over) break;
            }
            if (result) await applyRoundResult(result, {kind: 'challenge-end'});
        } catch (error) {
            finish('wrong');
        } finally {
            roundActionPending = false;
        }
    }

    function onRoundResult(result, action, before) {
        if (!run || !before?.has_track || before.game_over) return;
        const after = result.state;
        const won = result.success === true;
        if (!after.game_over) {
            if (run.mode === 'sudden_death' && action.kind === 'guess' && !won) {
                run.wrongGuess = true;
                endSongAfterWrongGuess();
            }
            return;
        }
        run.played += 1;
        if (won) {
            run.solved += 1;
            run.points += Math.max(0, after.score - before.score);
        }
        const rules = CHALLENGES[run.mode];
        if (run.mode === 'sudden_death' && !won) { finish(run.wrongGuess ? 'wrong' : 'missed'); return; }
        if (run.mode === 'three_lives' && !won) {
            run.lives -= 1;
            if (run.lives <= 0) { finish('lives'); return; }
        }
        if (run.mode === 'sprint' && run.played >= rules.songs) { finish('done'); return; }
        run.wrongGuess = false;
        paintHud();
        if (run.mode === 'time_attack') {
            advanceTimer = window.setTimeout(async () => {
                if (!run || run.mode !== 'time_attack') return;
                await startNextRound();
                autoPlay();
            }, won ? 1600 : 1200);
        }
    }

    function onState(state) {
        if (run && !state.authenticated) { finish('left'); return; }
        paintHud();
        if (!run) return;
        for (const id of ['reroll-btn', 'difficulty-select', 'recording-era-select']) {
            const control = $(id);
            if (control) control.disabled = true;
        }
        $('game-mode-title').textContent = CHALLENGES[run.mode].name;
        $('game-mode-description').textContent = CHALLENGES[run.mode].blurb;
    }

    function onLeaveChallenge() {
        if (run) finish('left');
    }

    async function onAccount(account) {
        if (!account?.authenticated) {
            bests = {};
            paintBests();
            return;
        }
        try {
            bests = await api().get_challenge_bests();
        } catch (error) {
            bests = {};
        }
        paintBests();
    }

    function paintBests() {
        document.querySelectorAll('[data-best-for]').forEach(node => {
            const best = bests[node.dataset.bestFor];
            node.textContent = best ? `Best ${unitText(node.dataset.bestFor, best.best_score)}` : 'No runs yet';
            node.classList.toggle('has-best', Boolean(best));
        });
        document.querySelectorAll('[data-challenge]').forEach(button => {
            button.classList.toggle('is-running', run?.mode === button.dataset.challenge);
        });
    }

    function paintHud() {
        const hud = $('challenge-hud');
        if (!hud) return;
        hud.classList.toggle('hidden', !run);
        if (!run) return;
        const rules = CHALLENGES[run.mode];
        $('challenge-name').textContent = rules.name;
        const meter = $('challenge-meter');
        meter.replaceChildren();
        meter.dataset.urgent = 'false';
        if (run.mode === 'three_lives') {
            for (let index = 0; index < rules.lives; index += 1) {
                meter.append(make('span', index < run.lives ? 'life is-on' : 'life', index < run.lives ? '♥' : '♡'));
            }
            meter.setAttribute('aria-label', `${run.lives} of ${rules.lives} lives left`);
        } else if (run.mode === 'time_attack') {
            const left = Math.max(0, Math.ceil(((run.endsAt || Date.now() + rules.seconds * 1000) - Date.now()) / 1000));
            meter.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
            meter.dataset.urgent = String(left <= 10);
            meter.setAttribute('aria-label', `${left} seconds left`);
        } else if (run.mode === 'sprint') {
            meter.textContent = `Song ${Math.min(run.played + 1, rules.songs)} / ${rules.songs}`;
            meter.setAttribute('aria-label', meter.textContent);
        } else {
            meter.textContent = run.solved ? `${run.solved} in a row` : 'No mistakes allowed';
            meter.setAttribute('aria-label', meter.textContent);
        }
        $('challenge-score').textContent = unitText(run.mode, score(run));
        const best = bests[run.mode];
        $('challenge-best').textContent = best ? `Best ${unitText(run.mode, best.best_score)}` : 'First run';
    }

    const REASONS = {
        wrong: 'A wrong guess ended the run.',
        missed: 'You ran out of attempts on a song.',
        lives: 'Out of lives.',
        time: "Time's up!",
        done: 'All ten songs played.',
        quit: 'You ended the run.',
    };

    function paintSummary() {
        const box = $('challenge-summary');
        if (!box) return;
        box.replaceChildren();
        box.classList.toggle('hidden', !lastRun);
        if (!lastRun) return;
        const rules = CHALLENGES[lastRun.mode];
        const head = make('div', 'summary-head');
        head.append(make('span', 'kicker kicker-c', `${rules.name} - run over`));
        if (lastRun.newBest) head.append(make('span', 'best-badge', 'New best'));
        box.append(head);
        box.append(make('p', 'summary-score tabular', unitText(lastRun.mode, score(lastRun))));
        box.append(make('p', 'small', REASONS[lastRun.reason] || ''));
        const stats = make('div', 'summary-stats');
        for (const [label, value] of [['Songs guessed', `${lastRun.solved} / ${lastRun.played}`], ['Points earned', lastRun.points.toLocaleString()],
                                      ['Your best', bests[lastRun.mode] ? unitText(lastRun.mode, bests[lastRun.mode].best_score) : '-']]) {
            const stat = make('div', 'stat');
            stat.append(make('p', 'stat-label', label), make('p', 'stat-value tabular', value));
            stats.append(stat);
        }
        box.append(stats);
        if (lastRun.saveError) box.append(make('p', 'small summary-error', lastRun.saveError));
        const actions = make('div', 'summary-actions');
        const again = make('button', 'btn btn-primary', 'Play again');
        again.type = 'button';
        again.addEventListener('click', () => start(lastRun.mode));
        const hub = make('button', 'btn btn-secondary', 'All games');
        hub.type = 'button';
        hub.addEventListener('click', () => { lastRun = null; paintSummary(); switchTab('hub'); });
        actions.append(again, hub);
        box.append(actions);
        if (!$('tab-game').classList.contains('hidden')) box.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }

    function paintAll() {
        paintHud();
        paintSummary();
        paintBests();
    }

    function init() {
        $('challenge-quit')?.addEventListener('click', () => finish('quit'));
        paintAll();
    }

    init();
    return {
        CHALLENGES, start, finish: () => finish('quit'), active: () => (run ? {...run} : null),
        onRoundResult, onState, onLeaveChallenge, onAccount,
    };
})();
