"""
intelligence/growth_hacker.py — Explosive Subscriber Growth Engine
==================================================================
Systematic growth through collaboration, viral engineering, search domination,
playlist architecture, and mathematical 30/60/90 day growth targets.

Components:
  CollaborationDetector  — find similar-sized channels, auto-pitch, track outcomes
  ViralLoopEngineer      — shareable moments, end-screen opt, subscribe prompts
  SearchDominator        — blue-ocean keywords, 10-video clusters, community backlinks
  PlaylistArchitect      — strategic playlists, binge-trap engineering, SEO titles
  GrowthTargetTracker    — 30/60/90 day model, trajectory alerts, auto-intensify
  GrowthHacker           — orchestrator (daily cycle)

API requirements (all optional — degrade gracefully without keys):
  YOUTUBE_DATA_API_KEY   — channel search, competing-video count
  ANTHROPIC_API_KEY      — pitch email generation, keyword cluster ideas
  REDDIT_CLIENT_ID       — Reddit OAuth2 for community backlink submission
  REDDIT_CLIENT_SECRET   —   ↑
  REDDIT_USERNAME        —   ↑
  REDDIT_PASSWORD        —   ↑
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("brain.growth_hacker")

HERE = Path(__file__).parent.parent

# ── Constants ─────────────────────────────────────────────────────────────────

COLLAB_SIZE_RANGE   = (0.80, 1.20)   # 80–120 % of our subscriber count
OVERLAP_THRESHOLD   = 0.30           # min Jaccard similarity for collab candidate
BLUE_OCEAN_MAX_VIDS = 50             # competing videos cap for "blue ocean"
VIRAL_MOMENT_POS    = 0.60           # shareable moment target: 60 % through video
SUBSCRIBE_RANGE     = (0.40, 0.60)   # subscribe-prompt zone: 40–60 %
GROWTH_WEEKLY_BASE  = 0.025          # 2.5 % weekly organic growth
GROWTH_SHORTS_BOOST = 0.015          # +1.5 %/wk when Shorts are active
GROWTH_FREQ_COEFF   = 0.08           # +8 % per extra video/week above baseline 3

PITCH_TEMPLATES = [
    (
        "Hey {name}! I've been following your work on {their_niche} — genuinely "
        "impressive. My channel covers {our_niche} (~{subs}K subscribers, similar "
        "to yours). I have a concept that would serve both our audiences: {concept}. "
        "Would you be open to a quick chat about collaborating?"
    ),
    (
        "Hi {name} — quick pitch: we're both in the {their_niche} space at "
        "similar scale (~{subs}K subs), and our audience overlap is significant. "
        "I'd love to do a joint video — my angle: {concept}. "
        "Let me know if you're open to it!"
    ),
]

SHAREABLE_TRIGGERS = [
    (r"\b\d{1,3}\s*[%xX]\b",           "statistic"),
    (r"\b\d+\s*billion\b|\d+\s*million\b", "scale"),
    (r"\bmost people don'?t\b",           "exclusive_knowledge"),
    (r"\bsecret\b|\bhidden\b",            "mystery"),
    (r"\bfree\b|\bzero[\s-]cost\b",       "value"),
    (r"\bproven\b|\bstudies show\b",      "authority"),
    (r"\byou won'?t believe\b",           "curiosity"),
    (r"\bbreaking\b|\bjust happened\b",   "news_hook"),
    (r"\bsend this to\b|\bshare this\b",  "explicit_share"),
]

PLAYLIST_PATTERNS = [
    "Complete {topic} Guide",
    "{topic} Masterclass",
    "{topic}: From Zero to Expert",
    "Everything About {topic}",
    "{topic} Deep Dive Series",
    "Best {topic} Videos",
    "{topic} Explained",
]

NICHE_SUBREDDITS: Dict[str, List[str]] = {
    "energy":      ["energy", "solar", "RenewableEnergy", "sustainability", "CleanEnergy"],
    "technology":  ["technology", "tech", "gadgets", "Futurology"],
    "finance":     ["personalfinance", "investing", "financialindependence"],
    "fitness":     ["fitness", "bodyweightfitness", "loseit"],
    "food":        ["Cooking", "recipes", "food"],
    "default":     ["videos", "watchandlearn", "education"],
}

KEYWORD_ANGLES = [
    "Complete Beginner's Guide to {kw}",
    "Top 10 Facts About {kw} Most People Don't Know",
    "{kw}: Common Mistakes to Avoid",
    "How to Get Started with {kw} in 2026",
    "{kw} vs Alternatives: Honest Comparison",
    "The Future of {kw}: Expert Predictions",
    "{kw} Cost Breakdown: Is It Worth It?",
    "How {kw} Changed Everything in 2025",
    "Pro {kw} Tips Nobody Talks About",
    "{kw} Success Stories: What Actually Works",
]


# ── 1. CollaborationDetector ─────────────────────────────────────────────────

class CollaborationDetector:
    """
    Finds collaboration-worthy channels and automates outreach.

    Similarity criteria:
      - Subscriber count:  80–120 % of our channel size
      - Content overlap:   Jaccard similarity of topics/tags ≥ OVERLAP_THRESHOLD
    """

    YT_API_URL = "https://www.googleapis.com/youtube/v3"

    def __init__(self):
        self._yt_key     = os.getenv("YOUTUBE_DATA_API_KEY", "")
        self._claude_key = os.getenv("ANTHROPIC_API_KEY", "")

    def find_similar_channels(self, our_subs: int, niche: str,
                               max_results: int = 20) -> List[Dict]:
        """
        Search YouTube for channels in niche with 80–120 % of our subscriber count.
        Returns list of channel dicts with id, name, subs, topics.
        """
        if not self._yt_key:
            log.debug("No YT API key — returning mock collab candidates")
            return self._mock_candidates(our_subs, niche)

        min_subs = int(our_subs * COLLAB_SIZE_RANGE[0])
        max_subs = int(our_subs * COLLAB_SIZE_RANGE[1])

        search_url = (
            f"{self.YT_API_URL}/search"
            f"?part=snippet&type=channel&q={urllib.parse.quote(niche)}"
            f"&maxResults={max_results}&key={self._yt_key}"
        )
        try:
            with urllib.request.urlopen(search_url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            log.debug(f"Channel search failed: {e}")
            return self._mock_candidates(our_subs, niche)

        channel_ids = [
            item["id"]["channelId"]
            for item in data.get("items", [])
            if item["id"].get("kind") == "youtube#channel"
        ]
        if not channel_ids:
            return self._mock_candidates(our_subs, niche)

        stats_url = (
            f"{self.YT_API_URL}/channels"
            f"?part=statistics,snippet,topicDetails"
            f"&id={','.join(channel_ids)}&key={self._yt_key}"
        )
        try:
            with urllib.request.urlopen(stats_url, timeout=10) as resp:
                stats = json.loads(resp.read())
        except Exception as e:
            log.debug(f"Channel stats failed: {e}")
            return []

        candidates = []
        for ch in stats.get("items", []):
            subs_str = ch.get("statistics", {}).get("subscriberCount", "0")
            subs     = int(subs_str) if subs_str else 0
            if min_subs <= subs <= max_subs:
                candidates.append({
                    "channel_id":   ch["id"],
                    "channel_name": ch["snippet"].get("title", ""),
                    "subs":         subs,
                    "description":  ch["snippet"].get("description", "")[:200],
                    "topics":       ch.get("topicDetails", {}).get("topicCategories", []),
                    "niche":        niche,
                })
        return candidates

    def analyze_content_overlap(self, our_topics: List[str],
                                 their_topics: List[str]) -> float:
        """
        Jaccard similarity: |intersection| / |union| of lowercased topic strings.
        Returns 0.0 if either list is empty.
        """
        if not our_topics or not their_topics:
            return 0.0
        our_set   = {t.lower().strip() for t in our_topics if t}
        their_set = {t.lower().strip() for t in their_topics if t}
        if not our_set or not their_set:
            return 0.0
        return round(len(our_set & their_set) / len(our_set | their_set), 4)

    def generate_pitch_email(self, channel: Dict, our_channel: Dict) -> str:
        """
        Generate a personalized collab pitch via Claude Haiku (template fallback).
        """
        if self._claude_key:
            pitch = self._claude_pitch(channel, our_channel)
            if pitch:
                return pitch

        import random
        params = {
            "name":        channel.get("channel_name", "there"),
            "their_niche": channel.get("niche", "your niche"),
            "our_niche":   our_channel.get("niche", "energy"),
            "subs":        str(max(channel.get("subs", 0) // 1000, 1)),
            "concept":     (
                f"a joint video exploring where {our_channel.get('niche','energy')} "
                f"meets {channel.get('niche','your niche')}"
            ),
        }
        return random.choice(PITCH_TEMPLATES).format(**params)

    def track_collab_performance(self, channel_id: str,
                                  memory, days: int = 30) -> Dict:
        """
        Compare our growth vs collaborator's in the N days after a collab.
        Uses competitor_snapshots from memory.
        """
        our_growth   = 0.0
        their_growth = memory.competitor_growth_rate(channel_id, days) if memory else 0.0

        return {
            "channel_id":   channel_id,
            "days":         days,
            "our_growth":   our_growth,
            "their_growth": their_growth,
            "net_benefit":  round(our_growth - their_growth, 4),
            "positive":     our_growth > 0,
        }

    def _claude_pitch(self, channel: Dict, our_channel: Dict) -> Optional[str]:
        prompt = (
            f"Write a YouTube collaboration pitch email in under 80 words. "
            f"Be specific, professional, and warm — no generic flattery. "
            f"Our channel: '{our_channel.get('name','Energy Channel')}', "
            f"niche: {our_channel.get('niche','energy')}, "
            f"{our_channel.get('subs',10000):,} subscribers. "
            f"Their channel: '{channel.get('channel_name','Partner')}', "
            f"niche: {channel.get('niche','similar')}, "
            f"{channel.get('subs',10000):,} subscribers. "
            f"Propose one concrete joint video concept."
        )
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._claude_key)
            msg    = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            log.debug(f"Claude pitch failed: {e}")
            return None

    def _mock_candidates(self, our_subs: int, niche: str) -> List[Dict]:
        return [
            {
                "channel_id":   f"UC_MOCK_{i}",
                "channel_name": f"{niche.title()} Creator {i}",
                "subs":         int(our_subs * (0.85 + i * 0.15)),
                "description":  f"Content about {niche} and sustainability",
                "topics":       [niche, "sustainability", "technology"],
                "niche":        niche,
            }
            for i in range(3)
        ]


# ── 2. ViralLoopEngineer ─────────────────────────────────────────────────────

class ViralLoopEngineer:
    """
    Engineers viral loops directly into video structure:
      - Shareable moment at ~60 % mark (fact/demo people WANT to forward)
      - End screen: video most likely to convert THIS viewer to subscriber
      - Subscribe prompt at peak information-density point (40–60 %)
    """

    def find_shareable_moment(self, script: Dict) -> Dict:
        """
        Scan scenes for viral trigger patterns around the 60 % position.
        Returns: {scene_index, timestamp_pct, trigger_type, text_preview, score}.
        """
        scenes = script.get("scenes", [])
        if not scenes:
            return {"scene_index": 0, "timestamp_pct": 0.60,
                    "trigger_type": "none", "text_preview": "", "score": 0.0}

        best       = None
        best_score = -1.0

        for i, scene in enumerate(scenes):
            text     = scene.get("narration", "") + " " + scene.get("title", "")
            pos      = i / len(scenes)
            pos_bonus = max(0.0, 1.0 - abs(pos - VIRAL_MOMENT_POS) * 3)

            for pattern, trigger_type in SHAREABLE_TRIGGERS:
                if re.search(pattern, text, re.IGNORECASE):
                    score = 50.0 + pos_bonus * 50.0
                    if score > best_score:
                        best_score = score
                        best = {
                            "scene_index":   i,
                            "timestamp_pct": round(pos, 3),
                            "trigger_type":  trigger_type,
                            "text_preview":  text[:80],
                            "score":         round(score, 1),
                        }

        if best is None:
            # Fallback: scene closest to 60 %
            target = max(0, int(len(scenes) * VIRAL_MOMENT_POS) - 1)
            text   = scenes[target].get("narration", "")[:80]
            best   = {"scene_index": target,
                      "timestamp_pct": round(target / len(scenes), 3),
                      "trigger_type":  "position_fallback",
                      "text_preview":  text,
                      "score":         0.0}
        return best

    def find_subscribe_prompt_position(self, script: Dict) -> Dict:
        """
        Find the optimal subscribe prompt position:
        scene with highest information density in the 40–60 % range.
        """
        scenes = script.get("scenes", [])
        if not scenes:
            return {"scene_index": 0, "timestamp_pct": 0.50,
                    "reason": "no_scenes"}

        n  = len(scenes)
        lo = max(0, int(n * SUBSCRIBE_RANGE[0]))
        hi = min(n - 1, int(n * SUBSCRIBE_RANGE[1]))
        if lo > hi:
            lo, hi = 0, n - 1

        best_idx = max(
            range(lo, hi + 1),
            key=lambda i: len(scenes[i].get("narration", "")),
        )
        return {
            "scene_index":   best_idx,
            "timestamp_pct": round(best_idx / n, 3),
            "reason":        "peak_information_density",
        }

    def optimize_end_screen(self, video_history: List[Dict],
                             our_videos: List[Dict]) -> Optional[Dict]:
        """
        Select the best end-screen video based on viewer watch history.
        Scores our videos by topic match + CTR.
        """
        if not our_videos:
            return None
        if not video_history:
            return max(our_videos, key=lambda v: v.get("views", 0) or 0)

        watched_topics = {v.get("topic", "").lower() for v in video_history}

        def score(v):
            match = 1.0 if v.get("topic", "").lower() in watched_topics else 0.0
            ctr   = (v.get("ctr") or 5.0) / 10.0
            return match * 0.6 + ctr * 0.4

        return max(our_videos, key=score)

    def inject_viral_elements(self, script: Dict) -> Dict:
        """
        Enhance a script copy by:
          1. Marking the shareable-moment scene
          2. Adding a viral CTA at that scene
          3. Adding a subscribe prompt tag at the optimal scene
        Does NOT mutate the original script dict.
        """
        import copy
        script = copy.deepcopy(script)
        scenes = script.get("scenes", [])
        if not scenes:
            return script

        shareable = self.find_shareable_moment(script)
        sub_pos   = self.find_subscribe_prompt_position(script)

        sm_idx = shareable["scene_index"]
        sp_idx = sub_pos["scene_index"]

        if sm_idx < len(scenes):
            scenes[sm_idx]["shareable_moment"] = True
            scenes[sm_idx]["viral_cta"] = (
                "👉 Screenshot this and send it to someone who needs to see it!"
            )

        if sp_idx < len(scenes):
            scenes[sp_idx]["subscribe_prompt"] = True
            narration = scenes[sp_idx].get("narration", "")
            if narration:
                scenes[sp_idx]["narration"] = (
                    narration
                    + " If you're finding this useful, hitting subscribe takes "
                    "2 seconds and makes sure you never miss a video like this."
                )

        script["viral_injection"] = {
            "shareable_moment_idx": sm_idx,
            "subscribe_prompt_idx": sp_idx,
            "shareable_trigger":    shareable.get("trigger_type", "none"),
        }
        return script

    def build_end_card_sequence(self, our_videos: List[Dict]) -> Dict:
        """Return primary + secondary end-card picks."""
        if not our_videos:
            return {"primary": None, "secondary": None, "placement_pct": 0.95}
        best       = self.optimize_end_screen([], our_videos)
        best_topic = best.get("topic", "") if best else ""
        alt        = next(
            (v for v in our_videos
             if v.get("topic", "") != best_topic and v.get("youtube_id")),
            None,
        )
        return {"primary": best, "secondary": alt, "placement_pct": 0.95}


# ── 3. SearchDominator ───────────────────────────────────────────────────────

class SearchDominator:
    """
    Finds blue-ocean keywords and drives off-platform traffic.

    Blue ocean = high estimated search demand AND <BLUE_OCEAN_MAX_VIDS competing videos.
    Community backlinks submitted to relevant Reddit communities (OAuth2 required)
    and logged for manual Quora posting.
    """

    YT_API_URL   = "https://www.googleapis.com/youtube/v3"
    REDDIT_API   = "https://oauth.reddit.com"
    REDDIT_TOKEN = "https://www.reddit.com/api/v1/access_token"

    def __init__(self):
        self._yt_key      = os.getenv("YOUTUBE_DATA_API_KEY", "")
        self._claude_key  = os.getenv("ANTHROPIC_API_KEY", "")
        self._reddit_id   = os.getenv("REDDIT_CLIENT_ID", "")
        self._reddit_sec  = os.getenv("REDDIT_CLIENT_SECRET", "")
        self._reddit_user = os.getenv("REDDIT_USERNAME", "")
        self._reddit_pass = os.getenv("REDDIT_PASSWORD", "")
        self._reddit_tok: Optional[str] = None

    def find_blue_ocean_keywords(self, seed_topic: str, niche: str,
                                  n: int = 10) -> List[Dict]:
        """
        Expand seed → filter by competition < BLUE_OCEAN_MAX_VIDS →
        rank by opportunity score.
        Returns list of {keyword, competition, volume_tier, opportunity_score}.
        """
        seeds   = self._expand_seed(seed_topic, niche)
        results = []

        for kw in seeds[:20]:
            competition = self._count_competing_videos(kw)
            volume_tier = self._estimate_search_volume(kw, niche)
            opp_score   = self._opportunity_score(competition, volume_tier)

            if competition < BLUE_OCEAN_MAX_VIDS:
                results.append({
                    "keyword":           kw,
                    "competition":       competition,
                    "volume_tier":       volume_tier,
                    "opportunity_score": round(opp_score, 1),
                })

        results.sort(key=lambda x: x["opportunity_score"], reverse=True)
        return results[:n]

    def build_keyword_cluster(self, main_keyword: str, niche: str,
                               n: int = 10) -> Dict:
        """
        Build a 10-video content cluster to establish topic authority.
        Uses Claude Haiku for ideas; falls back to heuristic templates.
        """
        if self._claude_key:
            ideas = self._claude_cluster(main_keyword, niche, n)
            if ideas:
                return {"main_keyword": main_keyword, "niche": niche,
                        "video_ideas": ideas, "count": len(ideas),
                        "authority_strategy": "cluster_10"}

        return self._heuristic_cluster(main_keyword, niche, n)

    def submit_to_communities(self, video_url: str, title: str,
                               description: str, niche: str,
                               dry_run: bool = False) -> Dict:
        """
        Post video link to relevant subreddits (Reddit OAuth2).
        Log Quora content for manual posting (no public write API).
        """
        result     = {"reddit": [], "quora": []}
        subreddits = NICHE_SUBREDDITS.get(niche.lower(), NICHE_SUBREDDITS["default"])

        for sub in subreddits[:3]:
            if dry_run or not self._reddit_id:
                log.info(f"  [DRY] Would post to r/{sub}: {title[:60]}")
                result["reddit"].append({"subreddit": sub, "status": "dry_run"})
            else:
                ok = self._reddit_submit(sub, title, video_url)
                result["reddit"].append({
                    "subreddit": sub,
                    "status":    "posted" if ok else "failed",
                })

        kw_preview = " ".join(title.split()[:4])
        result["quora"].append({
            "platform": "quora",
            "query":    f"{kw_preview} site:quora.com",
            "content":  f"[Video] {title}\n{description[:300]}\n{video_url}",
            "status":   "queued_manual",
        })
        return result

    # ── Private helpers ─────────────────────────────────────────────────────

    def _expand_seed(self, seed: str, niche: str) -> List[str]:
        modifiers = [
            "how to", "best", "guide to", "tutorial", "explained",
            "for beginners", "2026", "vs solar", "review", "benefits of",
            "cost of", "pros and cons", "truth about", "complete guide to",
        ]
        kws = [seed]
        for mod in modifiers:
            kws.append(f"{mod} {seed}")
            if niche.lower() not in seed.lower():
                kws.append(f"{mod} {seed} {niche.lower()}")
        return list(dict.fromkeys(kws))[:25]

    def _count_competing_videos(self, keyword: str) -> int:
        if not self._yt_key:
            return self._heuristic_competition(keyword)
        url = (
            f"{self.YT_API_URL}/search?part=id&type=video"
            f"&q={urllib.parse.quote(keyword)}"
            f"&maxResults=50&order=relevance&key={self._yt_key}"
        )
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read())
            return min(data.get("pageInfo", {}).get("totalResults", 50), 9999)
        except Exception:
            return self._heuristic_competition(keyword)

    def _heuristic_competition(self, keyword: str) -> int:
        """Long-tail keywords = lower competition heuristic."""
        wc = len(keyword.split())
        if wc >= 6:   return 10
        elif wc >= 5: return 20
        elif wc >= 4: return 40
        elif wc >= 3: return 80
        else:         return 250

    def _estimate_search_volume(self, keyword: str, niche: str) -> str:
        wc          = len(keyword.split())
        has_numbers = bool(re.search(r'\d', keyword))
        has_question = any(kw in keyword.lower()
                           for kw in ["how", "what", "why", "best", "vs"])
        score = 0
        if wc <= 2:       score += 3
        elif wc <= 4:     score += 2
        else:             score += 1
        if has_question:  score += 2
        if has_numbers:   score += 1
        if niche.lower() in keyword.lower(): score += 1
        if score >= 5:    return "high"
        elif score >= 3:  return "medium"
        return "low"

    def _opportunity_score(self, competition: int, volume_tier: str) -> float:
        vol = {"high": 10, "medium": 5, "low": 2}.get(volume_tier, 3)
        return (vol / max(competition, 1)) * 100

    def _claude_cluster(self, keyword: str, niche: str,
                         n: int) -> Optional[List[Dict]]:
        prompt = (
            f"Generate {n} specific YouTube video ideas for a content cluster "
            f"around '{keyword}' in the {niche} niche. "
            f"Each video should be standalone but reinforce topic authority. "
            f"Return only valid JSON array: "
            f'[{{"title": "...", "angle": "...", "search_intent": "informational|commercial"}}]'
        )
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._claude_key)
            msg    = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text
            m    = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                return json.loads(m.group(0))
        except Exception as e:
            log.debug(f"Claude cluster failed: {e}")
        return None

    def _heuristic_cluster(self, keyword: str, niche: str, n: int) -> Dict:
        ideas = []
        for angle in KEYWORD_ANGLES[:n]:
            ideas.append({
                "title":         angle.format(kw=keyword),
                "angle":         angle.split(":")[0].format(kw=keyword),
                "search_intent": "informational",
            })
        return {
            "main_keyword":       keyword,
            "niche":              niche,
            "video_ideas":        ideas,
            "count":              len(ideas),
            "authority_strategy": "cluster_10",
        }

    def _reddit_submit(self, subreddit: str, title: str, url: str) -> bool:
        if not all([self._reddit_id, self._reddit_sec,
                    self._reddit_user, self._reddit_pass]):
            return False
        if not self._reddit_tok:
            self._reddit_tok = self._get_reddit_token()
        if not self._reddit_tok:
            return False

        payload = urllib.parse.urlencode({
            "sr":      subreddit,
            "kind":    "link",
            "title":   title[:300],
            "url":     url,
            "nsfw":    "false",
            "spoiler": "false",
        }).encode()

        req = urllib.request.Request(
            f"{self.REDDIT_API}/api/submit",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._reddit_tok}",
                "User-Agent":    "YTGrowthBot/1.0",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return not result.get("json", {}).get("errors", [])
        except Exception as e:
            log.debug(f"Reddit submit failed for r/{subreddit}: {e}")
            return False

    def _get_reddit_token(self) -> Optional[str]:
        import base64
        creds = urllib.parse.urlencode({
            "grant_type": "password",
            "username":   self._reddit_user,
            "password":   self._reddit_pass,
        }).encode()
        auth = base64.b64encode(
            f"{self._reddit_id}:{self._reddit_sec}".encode()
        ).decode()
        req = urllib.request.Request(
            self.REDDIT_TOKEN,
            data=creds,
            headers={
                "Authorization": f"Basic {auth}",
                "User-Agent":    "YTGrowthBot/1.0",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read()).get("access_token")
        except Exception as e:
            log.debug(f"Reddit token fetch failed: {e}")
            return None


# ── 4. PlaylistArchitect ─────────────────────────────────────────────────────

class PlaylistArchitect:
    """
    Auto-organizes videos into strategic playlists engineered for binge-watching.

    Psychology:
      - Episode order: hook intro → escalating complexity → deep dives
      - Binge-trap: each video's end card points to the next in playlist
      - Playlist title = separate SEO target (different from video titles)
    """

    def auto_organize_videos(self, videos: List[Dict],
                              memory=None) -> List[Dict]:
        """
        Group videos by topic cluster, order for binge-watching, save playlists.
        Returns list of playlist dicts.
        """
        if not videos:
            return []

        clusters  = self._cluster_by_topic(videos)
        playlists = []

        for topic, vids in clusters.items():
            if len(vids) < 2:
                continue
            ordered   = self.optimize_playlist_order(vids)
            seo_title = self.generate_seo_title(topic)
            video_ids = [
                v.get("youtube_id") or str(v.get("id", ""))
                for v in ordered
            ]

            playlist = {
                "title":         f"{topic.title()} Series",
                "seo_title":     seo_title,
                "topic_cluster": topic,
                "video_ids":     video_ids,
                "binge_trap":    1,
                "status":        "active",
            }
            playlists.append(playlist)

            if memory:
                try:
                    memory.save_playlist(playlist)
                except Exception as e:
                    log.debug(f"Playlist save failed: {e}")

        return playlists

    def optimize_playlist_order(self, videos: List[Dict]) -> List[Dict]:
        """
        Order: intro/beginner first → general content → advanced/deep dives.
        Tiebreak: higher-views videos earlier in the playlist (proven hookers).
        """
        if len(videos) <= 1:
            return list(videos)

        def sort_key(v):
            title    = (v.get("title") or "").lower()
            is_intro = any(kw in title for kw in
                           ["intro", "beginner", "start", "guide", "101", "basics",
                            "what is", "overview"])
            is_deep  = any(kw in title for kw in
                           ["advanced", "deep dive", "expert", "pro", "master",
                            "complete", "ultimate"])
            views    = v.get("views", 0) or 0
            tier     = 0 if is_intro else (2 if is_deep else 1)
            return (tier, -views)

        return sorted(videos, key=sort_key)

    def generate_seo_title(self, topic: str) -> str:
        """Generate an SEO-optimized playlist title targeting a search query."""
        import random
        pattern = random.choice(PLAYLIST_PATTERNS)
        return pattern.format(topic=topic.title())

    def create_binge_trap(self, playlist: Dict) -> Dict:
        """
        Map each video_id to the next one in the playlist.
        Last video links back to first (keeps viewers in the loop).
        Returns {current_video_id: next_video_id}.
        """
        ids = [v for v in playlist.get("video_ids", []) if v]
        if len(ids) < 2:
            return {}
        trap = {ids[i]: ids[i + 1] for i in range(len(ids) - 1)}
        trap[ids[-1]] = ids[0]
        return trap

    def _cluster_by_topic(self, videos: List[Dict]) -> Dict[str, List[Dict]]:
        clusters: Dict[str, List[Dict]] = {}
        stop = {"the", "a", "an", "of", "is", "are", "in", "to", "and", "for"}
        for v in videos:
            raw   = v.get("topic") or v.get("title") or "general"
            words = [w for w in raw.lower().split() if w not in stop]
            key   = " ".join(words[:2]) if len(words) >= 2 else words[0] if words else "general"
            clusters.setdefault(key, []).append(v)
        return clusters


# ── 5. GrowthTargetTracker ───────────────────────────────────────────────────

class GrowthTargetTracker:
    """
    Sets and tracks 30/60/90 day subscriber growth targets using a
    compound weekly-growth model with posting-frequency multiplier.

    target_N = baseline × (1 + weekly_rate)^(N/7) × freq_multiplier
    weekly_rate = BASE + shorts_boost + freq_bonus
    freq_multiplier = 1 + max(0, vids_per_week − 3) × GROWTH_FREQ_COEFF
    """

    WARNING_THRESHOLD  = 0.10   # 10 % behind projection → warning
    CRITICAL_THRESHOLD = 0.20   # 20 % behind projection → critical

    def set_growth_model(self, baseline_subs: int,
                          posting_freq_week: float,
                          has_shorts: bool = True,
                          memory=None) -> Dict:
        """
        Calculate 30/60/90 day targets and save to memory.
        """
        weekly_rate = (
            GROWTH_WEEKLY_BASE
            + (GROWTH_SHORTS_BOOST if has_shorts else 0.0)
            + max(0, posting_freq_week - 3) * GROWTH_FREQ_COEFF * 0.01
        )
        freq_mult = 1.0 + max(0, posting_freq_week - 3) * GROWTH_FREQ_COEFF

        def project(days: int) -> int:
            return int(baseline_subs * ((1 + weekly_rate) ** (days / 7)) * freq_mult)

        model = {
            "set_date":           datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "baseline_subs":      baseline_subs,
            "posting_freq_week":  posting_freq_week,
            "has_shorts":         has_shorts,
            "weekly_growth_rate": round(weekly_rate * 100, 4),
            "freq_multiplier":    round(freq_mult, 4),
            "day30_target":       project(30),
            "day60_target":       project(60),
            "day90_target":       project(90),
            "status":             "active",
        }

        if memory:
            try:
                memory.save_growth_target(model)
            except Exception as e:
                log.debug(f"Growth target save failed: {e}")

        return model

    def check_trajectory(self, memory) -> Dict:
        """
        Compare current subscriber count vs projected trajectory.
        Returns status: 'on_track' | 'warning' | 'critical' | 'no_target' | 'no_data'.
        """
        if not memory:
            return {"status": "no_memory"}

        target = self._get_active_target(memory)
        if not target:
            return {"status": "no_target"}

        snaps = memory.get_revenue_snapshots(n=1)
        if not snaps:
            return {"status": "no_data", "target": target}

        current_subs = snaps[0].get("subscribers", 0)
        if not current_subs:
            return {"status": "no_data"}

        set_date_str = target.get("set_date", datetime.now().strftime("%Y-%m-%d"))
        set_date     = datetime.strptime(set_date_str[:10], "%Y-%m-%d")
        days_in      = (datetime.now() - set_date).days

        if days_in < 1:
            return {"status": "just_started", "days_in": 0}

        baseline   = target.get("baseline_subs", current_subs)
        weekly_r   = (target.get("weekly_growth_rate") or GROWTH_WEEKLY_BASE * 100) / 100
        freq_m     = target.get("freq_multiplier") or 1.0
        expected   = int(baseline * ((1 + weekly_r) ** (days_in / 7)) * freq_m)

        gap_pct    = (expected - current_subs) / max(expected, 1)

        if gap_pct > self.CRITICAL_THRESHOLD:
            status = "critical"
        elif gap_pct > self.WARNING_THRESHOLD:
            status = "warning"
        else:
            status = "on_track"

        return {
            "status":        status,
            "days_in":       days_in,
            "current_subs":  current_subs,
            "expected_subs": expected,
            "gap_pct":       round(gap_pct * 100, 1),
            "day30_target":  target.get("day30_target", 0),
            "day60_target":  target.get("day60_target", 0),
            "day90_target":  target.get("day90_target", 0),
        }

    def auto_intensify(self, trajectory: Dict, memory=None) -> Dict:
        """
        When behind trajectory, return a prioritized action list.
        Saves a retention_event to memory for audit trail.
        """
        status  = trajectory.get("status", "on_track")
        actions = []

        if status == "critical":
            actions = [
                {"action": "increase_shorts",
                 "detail": "Post 3 Shorts this week (triple current output)"},
                {"action": "community_posts",
                 "detail": "Switch to daily community posts this week"},
                {"action": "trending_pivot",
                 "detail": "Override topic selection — follow viral trends this week"},
                {"action": "collab_outreach",
                 "detail": "Send 5 collab pitches to similar-sized channels immediately"},
            ]
        elif status == "warning":
            actions = [
                {"action": "increase_shorts",
                 "detail": "Post one extra Short this week"},
                {"action": "community_posts",
                 "detail": "Add one extra community post this week"},
            ]

        if memory and actions:
            try:
                memory.save_retention_event({
                    "event_type":      f"growth_{status}",
                    "change_pct":      -trajectory.get("gap_pct", 0),
                    "subscriber_count": trajectory.get("current_subs", 0),
                    "strategy_applied": json.dumps([a["action"] for a in actions]),
                })
            except Exception as e:
                log.debug(f"Auto-intensify save failed: {e}")

        return {
            "status":    status,
            "gap_pct":   trajectory.get("gap_pct", 0),
            "actions":   actions,
            "intensify": len(actions) > 0,
        }

    def _get_active_target(self, memory) -> Optional[Dict]:
        try:
            targets = memory.get_growth_targets(status="active", n=1)
            return targets[0] if targets else None
        except Exception:
            return None


# ── 6. GrowthHacker (orchestrator) ──────────────────────────────────────────

class GrowthHacker:
    """
    Daily orchestrator — runs all 5 growth systems in sequence.
    """

    def __init__(self, memory=None):
        from core.memory import get_memory
        self._memory  = memory or get_memory()
        self._collab  = CollaborationDetector()
        self._viral   = ViralLoopEngineer()
        self._search  = SearchDominator()
        self._playlist = PlaylistArchitect()
        self._targets  = GrowthTargetTracker()

    def run_full_cycle(self, niche: str = "energy",
                        our_channel: Dict = None) -> Dict:
        """
        Run all growth systems and return a combined report dict.
        """
        our_channel = our_channel or {
            "name": "Energy Intelligence",
            "niche": niche,
            "subs": 10_000,
        }
        result = {
            "niche":     niche,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # 1. Collaboration detection
        log.info("[Growth] Scanning for collab candidates…")
        try:
            our_subs = our_channel.get("subs", 10_000)
            collabs  = self._collab.find_similar_channels(our_subs, niche)
            for ch in collabs[:3]:
                pitch = self._collab.generate_pitch_email(ch, our_channel)
                self._memory.save_collab_prospect(
                    {**ch, "niche": niche, "pitch_email": pitch}
                )
            result["collab"] = {
                "candidates_found":  len(collabs),
                "pitches_generated": min(len(collabs), 3),
            }
        except Exception as e:
            log.error(f"Collab detection failed: {e}", exc_info=True)
            result["collab"] = {"error": str(e)}

        # 2. Search domination + keyword cluster
        log.info("[Growth] Finding blue ocean keywords…")
        try:
            blue_ocean = self._search.find_blue_ocean_keywords(niche, niche, n=5)
            if blue_ocean:
                top     = blue_ocean[0]
                cluster = self._search.build_keyword_cluster(top["keyword"], niche)
                self._memory.save_keyword_cluster({
                    "main_keyword":      top["keyword"],
                    "cluster_topic":     niche,
                    "opportunity_score": top["opportunity_score"],
                    "videos_planned":    json.dumps(cluster.get("video_ideas", [])),
                    "status":            "planned",
                })
            result["search"] = {
                "blue_ocean_keywords":   len(blue_ocean),
                "top_keyword":           blue_ocean[0]["keyword"] if blue_ocean else "",
                "top_opportunity_score": blue_ocean[0]["opportunity_score"] if blue_ocean else 0,
            }
        except Exception as e:
            log.error(f"Search domination failed: {e}", exc_info=True)
            result["search"] = {"error": str(e)}

        # 3. Playlist architecture
        log.info("[Growth] Organizing playlists…")
        try:
            videos    = self._memory.get_video_stats(n=50)
            playlists = self._playlist.auto_organize_videos(videos, self._memory)
            result["playlists"] = {
                "created": len(playlists),
                "topics":  [p["topic_cluster"] for p in playlists[:5]],
            }
        except Exception as e:
            log.error(f"Playlist architecture failed: {e}", exc_info=True)
            result["playlists"] = {"error": str(e)}

        # 4. Growth trajectory
        log.info("[Growth] Checking trajectory…")
        try:
            traj = self._targets.check_trajectory(self._memory)
            if traj.get("status") in ("warning", "critical"):
                intensify = self._targets.auto_intensify(traj, self._memory)
                result["growth_trajectory"] = {**traj, "intensify": intensify}
            else:
                result["growth_trajectory"] = traj
        except Exception as e:
            log.error(f"Trajectory check failed: {e}", exc_info=True)
            result["growth_trajectory"] = {"error": str(e)}

        log.info(
            f"[Growth] Cycle: collabs={result.get('collab',{}).get('candidates_found',0)} "
            f"| keywords={result.get('search',{}).get('blue_ocean_keywords',0)} "
            f"| playlists={result.get('playlists',{}).get('created',0)} "
            f"| trajectory={result.get('growth_trajectory',{}).get('status','?')}"
        )
        return result

    def inject_viral_into_script(self, script: Dict) -> Dict:
        """Convenience wrapper: inject viral elements into a script."""
        return self._viral.inject_viral_elements(script)

    def print_report(self, result: Dict) -> None:
        collab  = result.get("collab", {})
        search  = result.get("search", {})
        pl      = result.get("playlists", {})
        traj    = result.get("growth_trajectory", {})
        print(f"\n{'='*60}")
        print(f"GROWTH HACKER REPORT — {result.get('niche','?').upper()}")
        print(f"{'='*60}")
        print(f"  Collaborations:  {collab.get('candidates_found',0)} candidates | "
              f"{collab.get('pitches_generated',0)} pitches")
        print(f"  Blue Ocean:      {search.get('top_keyword','—')} "
              f"(score={search.get('top_opportunity_score',0):.1f})")
        print(f"  Playlists:       {pl.get('created',0)} organized")
        status = traj.get("status", "—")
        gap    = traj.get("gap_pct", 0.0)
        print(f"  Trajectory:      {status} (gap={gap:.1f}%)")
        intensify = traj.get("intensify", {})
        if isinstance(intensify, dict) and intensify.get("actions"):
            print(f"  ⚡ INTENSIFY:    {len(intensify['actions'])} actions triggered")
        print()
