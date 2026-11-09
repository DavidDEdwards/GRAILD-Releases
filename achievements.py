"""Achievement catalog and persistent per-account progress."""

ACHIEVEMENTS = []

PROFILE_TAGS = {
    "owner": {"id": "owner", "name": "Owner / Creator", "kind": "assigned",
              "description": "Verified owner / creator of GRAIL'D."},
    "beta_tester": {
        "id": "beta_tester", "name": "Beta Tester", "kind": "assigned",
        "description": "Awarded by the owner for helping beta test GRAIL'D.",
    },
    "top_earner": {
        "id": "top_earner", "name": "Top earner", "kind": "earned",
        "description": "Unlocked all 50 achievements.",
    },
}

def milestones(category, metric, targets, names, requirement):
    for target, name in zip(targets, names):
        ACHIEVEMENTS.append({"id": f"{metric}_{target}", "name": name,
                             "category": category, "metric": metric,
                             "target": target, "description": requirement.format(n=f"{target:,}")})

milestones("Recognition", "correct", [1, 10, 25, 50, 100, 250, 500, 999],
           ["First Hit", "Finding Your Sound", "On the Wavelength", "Fifty Shades of Juice",
            "Century Club", "Deep Cut Detective", "Human Shazam", "999 Forever"],
           "Make {n} correct song guesses in total.")
milestones("Points", "points", [100, 500, 1000, 2500, 5000, 10000, 25000, 99999],
           ["On the Board", "Pocket Change", "Four Figures", "Stacking Up", "High Roller",
            "Five Figures", "Vault Builder", "999 Fortune"],
           "Earn {n} lifetime points.")
milestones("Streaks", "streak", [3, 5, 10, 15, 25, 50, 99],
           ["Three-Peat", "Heating Up", "Unstoppable", "In the Zone", "Purple Flame",
            "Never Miss a Beat", "Streak Legend"],
           "Win {n} rounds in a row without losing a round. Wrong guesses within a winning round are allowed.")
milestones("Collection", "grails", [1, 5, 10, 25, 50, 100, 250],
           ["First Grail", "Crate Digger", "Hidden Gems", "Grail Hunter", "Private Vault",
            "Archive Keeper", "Museum of Juice"],
           "Correctly identify {n} different songs to collect their grails.")
milestones("Dedication", "rounds", [10, 50, 100, 250, 500, 1000],
           ["Settling In", "Regular Listener", "On Repeat", "Long Session", "No Skips in Spirit",
            "Thousand-Round Tour"],
           "Finish {n} rounds, won or lost. Changing or rerolling a song does not count.")
milestones("Quick Ears", "first_try", [1, 10, 25, 50, 100, 250],
           ["Instant Recognition", "One-Shot Wonder", "Quick Draw", "Split-Second Ear",
            "First-Try Specialist", "One Second Legend"],
           "Win {n} rounds on the first attempt, without a wrong guess or skipped attempt. Tracked since the achievements update.")
milestones("Hard Mode", "hard", [1, 10, 25, 50, 100, 250],
           ["Hard Debut", "Pressure Tested", "Hard-Hitting", "Against the Odds", "Expert Ear",
            "Hard Mode Royalty"],
           "Win {n} rounds on Hard difficulty. Tracked since the achievements update.")
for provider, name in [("google", "Google Connected"), ("discord", "Discord Connected")]:
    ACHIEVEMENTS.append({"id": f"linked_{provider}", "name": name,
                         "category": "Connections", "metric": provider, "target": 1,
                         "description": f"Link a verified {provider.title()} account to your profile."})


def account_achievements(connection, user_id, now):
    account = connection.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    if not account:
        return {"entries": [], "unlocked": 0, "total": len(ACHIEVEMENTS)}
    counters = {row["metric"]: row["value"] for row in connection.execute(
        "SELECT metric, value FROM achievement_counters WHERE user_id = ?", (user_id,))}
    providers = {row["provider"] for row in connection.execute(
        "SELECT provider FROM external_identities WHERE user_id = ?", (user_id,))}
    metrics = {"correct": account["total_correct"], "points": account["total_points"],
               "streak": account["best_streak"], "rounds": account["songs_played"],
               "grails": connection.execute("SELECT COUNT(*) FROM grails WHERE user_id = ?", (user_id,)).fetchone()[0],
               "first_try": counters.get("first_try", 0), "hard": counters.get("hard", 0),
               "google": int("google" in providers), "discord": int("discord" in providers)}
    earned = {row["achievement_id"]: row["unlocked_at"] for row in connection.execute(
        "SELECT achievement_id, unlocked_at FROM account_achievements WHERE user_id = ?", (user_id,))}
    entries = []
    for item in ACHIEVEMENTS:
        progress = min(metrics[item["metric"]], item["target"])
        if progress >= item["target"] and item["id"] not in earned:
            connection.execute("INSERT OR IGNORE INTO account_achievements (user_id, achievement_id, unlocked_at) VALUES (?, ?, ?)",
                               (user_id, item["id"], now))
            earned[item["id"]] = now
        unlocked = item["id"] in earned
        entries.append({**item, "progress": item["target"] if unlocked else progress,
                        "unlocked": unlocked, "unlocked_at": earned.get(item["id"])})
    return {"entries": entries, "unlocked": sum(item["unlocked"] for item in entries), "total": len(ACHIEVEMENTS)}


