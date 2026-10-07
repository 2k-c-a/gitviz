"""Expanded output limits, in Unicode characters (not bytes or tokens)."""
REVIEW_LIMITS = {'title': 500, 'condition': 6000, 'impact': 6000, 'verification': 6000,
                 'suggestion': 6000, 'location.quote': 2000, 'limitations[]': 1000}
SUMMARY_LIMITS = {'overview': 8000, 'author_intent': 4000, 'file_changes[].summary': 3000, 'unknowns[]': 2000}
