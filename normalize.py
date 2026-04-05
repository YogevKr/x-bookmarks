"""Normalize categories in categorized.json — merge duplicates, collapse long tail."""

import json
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).parent
CATEGORIZED_FILE = BASE_DIR / "categorized.json"

# Maps messy category → canonical category.
# Unmapped categories with count >= MIN_COUNT are kept as-is.
# Unmapped categories below MIN_COUNT are dropped (bookmark keeps its other categories).
MERGE_MAP = {
    # Security cluster
    "Cybersecurity": "Security",
    "Security & Privacy": "Security",
    "Security & Hacking": "Security",
    "Cybersecurity & Hacking": "Security",
    "DevSecOps": "Security",
    "Threat Intelligence": "Security",
    "Privacy & Security": "Security",
    "Security & OSINT": "Security",
    "Security & Infrastructure": "Security",
    "Geopolitics & Security": "Security",
    "Hardware & Security": "Security",
    "OSINT & Intelligence": "Security",
    "OSINT": "Security",
    "AI Safety": "Security",

    # Infrastructure / DevOps cluster
    "Infrastructure": "DevTools",
    "Cloud Infrastructure": "DevTools",
    "Cloud Computing": "DevTools",
    "DevOps & Infrastructure": "DevTools",
    "Distributed Systems": "DevTools",
    "System Design": "DevTools",
    "Monitoring": "DevTools",
    "Developer Workflows": "DevTools",
    "Security & Infrastructure": "DevTools",

    # Software Engineering adjacent
    "Web Development": "Software Engineering",
    "Mobile Development": "Software Engineering",
    "Programming Languages": "Software Engineering",
    "Data Engineering": "Software Engineering",
    "Web Scraping": "Software Engineering",

    # Data / ML cluster
    "Data Science": "AI & Machine Learning",
    "Machine Learning": "AI & Machine Learning",
    "Data & Analytics": "AI & Machine Learning",
    "Data Analysis": "AI & Machine Learning",
    "Data Visualization": "AI & Machine Learning",
    "Analytics": "AI & Machine Learning",
    "Statistics": "AI & Machine Learning",
    "Mathematics": "AI & Machine Learning",
    "Mathematics & Science": "AI & Machine Learning",

    # Hardware cluster
    "Hardware & Infrastructure": "Hardware",
    "Hardware & Setup": "Hardware",
    "Hardware & DIY": "Hardware",
    "Hardware & Peripherals": "Hardware",
    "Hardware & Electronics": "Hardware",
    "Hardware & Devices": "Hardware",
    "Hardware & Open Source": "Hardware",
    "Hardware & IoT": "Hardware",
    "Technology & Hardware": "Hardware",
    "Robotics & Hardware": "Hardware",
    "Amateur Radio & Electronics": "Hardware",
    "Smart Home": "Hardware",
    "Smart Home & IoT": "Hardware",
    "IoT": "Hardware",
    "AR/VR": "Hardware",
    "XR & Emerging Tech": "Hardware",

    # Business / Finance cluster
    "Finance": "Personal Finance",
    "Investing": "Personal Finance",
    "Crypto & Trading": "Personal Finance",
    "Crypto & Web3": "Personal Finance",
    "Crypto & Blockchain": "Personal Finance",
    "Cryptocurrency": "Personal Finance",
    "Economics & Markets": "Personal Finance",
    "Cost Analysis": "Personal Finance",
    "Business": "Startups & Business",
    "Business Strategy": "Startups & Business",
    "SaaS": "Startups & Business",
    "Marketing & SEO": "Startups & Business",
    "SEO": "Startups & Business",

    # Product cluster
    "Product Development": "Startups & Business",
    "Product Design": "Design",
    "Product Management": "Startups & Business",
    "Product": "Startups & Business",

    # Career / Personal Dev cluster
    "Career Development": "Personal Development",
    "Career & Learning": "Personal Development",
    "Leadership & Management": "Personal Development",
    "Leadership": "Personal Development",
    "Books & Learning": "Personal Development",
    "Books": "Personal Development",
    "Educational Resources": "Personal Development",
    "Education": "Personal Development",
    "Academic Research": "Personal Development",
    "Research & Academia": "Personal Development",

    # Productivity cluster
    "Personal Productivity": "Productivity",
    "Productivity & Workflows": "Productivity",
    "Productivity & Work": "Productivity",
    "macOS": "Productivity",

    # News / Politics / Geopolitics cluster
    "News & Politics": "News & Current Events",
    "Geopolitics": "News & Current Events",
    "Geopolitics & Current Events": "News & Current Events",
    "Geopolitics & News": "News & Current Events",
    "Geopolitics & Policy": "News & Current Events",
    "Politics & Society": "News & Current Events",
    "Politics": "News & Current Events",
    "Politics & Policy": "News & Current Events",
    "Politics & Government": "News & Current Events",
    "Government & Policy": "News & Current Events",
    "History & Politics": "News & Current Events",
    "History": "News & Current Events",
    "Tech History": "News & Current Events",
    "Military & Conflict": "News & Current Events",
    "Military & Warfare": "News & Current Events",
    "News & Analysis": "News & Current Events",
    "News & Media": "News & Current Events",
    "News": "News & Current Events",
    "Technology News": "News & Current Events",
    "Documentaries & News": "News & Current Events",
    "Journalism & Investigation": "News & Current Events",
    "Fact-Checking": "News & Current Events",
    "Media & Free Speech": "News & Current Events",
    "Media & News": "News & Current Events",

    # Health / Lifestyle cluster
    "Health & Wellness": "Lifestyle",
    "Lifestyle & Wellness": "Lifestyle",
    "Personal Health & Wellness": "Lifestyle",
    "Health & Biology": "Lifestyle",
    "Travel & Lifestyle": "Lifestyle",
    "Travel & Geography": "Lifestyle",
    "Travel": "Lifestyle",
    "Lifestyle & Preparedness": "Lifestyle",
    "Sports": "Lifestyle",
    "Luxury & Watches": "Lifestyle",
    "Climate & Energy": "Lifestyle",
    "Sustainability": "Lifestyle",
    "Philosophy & Ethics": "Lifestyle",

    # Misc → drop (too vague)
    "General": None,
    "Technology": None,
    "Technology & Innovation": None,
    "Tech Trends": None,
    "Recommendations": None,
    "Consumer Tech": None,
    "Consumer Products": None,

    # Small clusters → merge up
    "Content Strategy": "Startups & Business",
    "Social Media": "Startups & Business",
    "Social Media & Web": "Startups & Business",
    "Open Source": "Software Engineering",
    "Research": "AI & Machine Learning",
    "Science & Research": "AI & Machine Learning",
    "Media": "News & Current Events",
    "Culture & Society": "News & Current Events",
    "Legal & Compliance": "News & Current Events",
    "Legal": "News & Current Events",
    "Podcasts & Audio": "Lifestyle",
    "Games & Projects": "Humor & Memes",
    "Design": "Design",
    "Databases": "DevTools",
}


def run_normalization():
    with open(CATEGORIZED_FILE) as f:
        data = json.load(f)

    changes = 0
    dropped = 0

    for bm in data["bookmarks"]:
        ai = bm.get("ai")
        if not ai:
            continue
        old_cats = ai.get("categories", [])
        new_cats = []
        seen = set()
        for cat in old_cats:
            mapped = MERGE_MAP.get(cat, cat)
            if mapped is None:
                dropped += 1
                continue
            if mapped != cat:
                changes += 1
            if mapped not in seen:
                new_cats.append(mapped)
                seen.add(mapped)
        ai["categories"] = new_cats

    with open(CATEGORIZED_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Print new stats
    cats = Counter()
    for bm in data["bookmarks"]:
        for c in bm.get("ai", {}).get("categories", []):
            cats[c] += 1

    print(f"Normalized: {changes} remapped, {dropped} dropped")
    print(f"Categories: {len(cats)} (was 154)")
    print()
    for cat, count in cats.most_common():
        print(f"  {count:4d}  {cat}")
