import argparse
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Error: PyMuPDF library is missing. Install with: pip install PyMuPDF")
    sys.exit(1)

def clean_formulas(text):
    """Specifically targets FRM-style math artifacts (Gamma, Delta, subscripts)."""
    # 1. Join character fragments by stripping spaces around math symbols
    text = re.sub(r'\s*_\s*', '_', text)
    text = re.sub(r'\s*{\s*', '{', text)
    text = re.sub(r'\s*}\s*', '}', text)
    
    # 2. Fix specific subscripts like Gamma_p or Gamma_{p}
    text = re.sub(r'\{?(Gamma|Delta|sigma|delta|gamma|theta|rho|vega)\}?_?\{?([a-zA-Z0-9pT])\}?', r'\1_\2', text)
    
    # 3. Prettify common symbols for the terminal
    replacements = {
        'Gamma': 'Γ (Gamma)', 'Delta': 'Δ (Delta)', 'sigma': 'σ (sigma)',
        'delta': 'δ (delta)', 'gamma': 'γ (gamma)', 'theta': 'θ (theta)',
        'rho': 'ρ (rho)', 'vega': 'ν (vega)'
    }
    for word, sym in replacements.items():
        text = text.replace(word, sym)
    
    # 4. Clean up remaining LaTeX artifacts
    text = text.replace('\\', '').replace('{', '').replace('}', '')
    
    # 5. Clean up fraction spacing like -( Gamma_p / Gamma_T )
    text = re.sub(r'-\s*\(\s*([^/]+)\s*/\s*([^)]+)\s*\)', r'-(\1/\2)', text)
    
    return re.sub(r' +', ' ', text)

def extract_text_robust(pdf_path):
    """Word-level extraction to preserve table columns and heal fragments."""
    full_text = ""
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            words = page.get_text("words") 
            if not words: continue

            # Group words into lines by Y-coordinate
            lines = {}
            for w in words:
                y_coord = round(w[1] / 3) * 3 # 3px vertical tolerance
                if y_coord not in lines: lines[y_coord] = []
                lines[y_coord].append(w)

            for y in sorted(lines.keys()):
                line_words = sorted(lines[y], key=lambda w: w[0])
                line_str = ""
                prev_x1 = -1
                for w in line_words:
                    x0, _, x1, _, word = w[:5]
                    if prev_x1 != -1:
                        gap = x0 - prev_x1
                        if gap > 20: line_str += "    "  # Column/Table gap
                        elif gap > 1.5: line_str += " "  # Standard space
                    line_str += word
                    prev_x1 = x1
                full_text += line_str + "\n"
        doc.close()
    except Exception as e:
        print(f"Error reading PDF: {e}")
        sys.exit(1)
    return full_text

def format_ttr(text):
    """Sentence-based 'Things to Remember' with junk filtering."""
    if not text: return ""
    clean = re.sub(r'^Things\s+to\s+Remember\s*', '', text, flags=re.IGNORECASE).strip()
    lines = [l.strip() for l in clean.split('\n') if l.strip()]
    valid = [l for l in lines if not (l.isdigit() or "Reading" in l or "Analyst" in l)]
    
    full_block = " ".join(valid)
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', full_block)
    return "\n".join([f" • {s.strip()}" for s in sentences if s.strip()])

def parse_questions(text):
    # Splits by "Q.123", "Q 123", or "Question 123"
    parts = re.split(r'((?:Q|Question)\s*\.?\s*\d+)', text, flags=re.IGNORECASE)
    questions = []
    
    ans_regex = re.compile(r'(?:The\s+)?correct\s+answer\s+(?:is|was)\s*:?\s*([A-D])', re.IGNORECASE)

    for i in range(1, len(parts) - 1, 2):
        q_id_raw = parts[i].strip()
        # Standardize ID for matching (e.g., Q.939)
        match = re.search(r'\d+', q_id_raw)
        q_id = f"Q.{match.group()}" if match else q_id_raw
        
        content = parts[i+1]
        ans_match = ans_regex.search(content)
        if not ans_match: continue # Skip if no clear answer found
            
        correct_opt = ans_match.group(1).upper()
        ans_idx = ans_match.start()
        
        q_body = clean_formulas(content[:ans_idx].strip())
        rem = content[ans_idx:].strip()
        
        ttr_match = re.search(r'Things\s+to\s+Remember', rem, re.IGNORECASE)
        if ttr_match:
            explanation = clean_formulas(rem[:ttr_match.start()].strip())
            # Format 'Alternate approach' into its own section
            explanation = re.sub(r'(Alternate\s+approach:?)', r'\n\n>> \1', explanation, flags=re.IGNORECASE)
            ttr = format_ttr(rem[ttr_match.start():].strip())
        else:
            explanation = clean_formulas(rem)
            ttr = ""
            
        questions.append({'id': q_id, 'text': q_id + "\n" + q_body, 'correct': correct_opt, 'explanation': explanation, 'ttr': ttr})
    return questions

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs='?')
    parser.add_argument("--after", dest="after_q")
    args = parser.parse_args()
    
    pdf_file = args.file or input("Enter PDF path: ").strip()
    print(f"Opening {pdf_file}...")
    
    full_text = extract_text_robust(pdf_file)
    # Global cleanup of footer junk
    full_text = re.sub(r'©\s*\d{4}-\d{4}\s*Analyst\s*Prep\.', '', full_text)
    full_text = re.sub(r'---\s*PAGE\s*\d+\s*---', '', full_text)
    
    questions = parse_questions(full_text)
    
    if not questions:
        print("\n[ERROR] No questions found.")
        print("Sample of extracted text for debugging:")
        print("-" * 50)
        print(full_text[:800])
        print("-" * 50)
        sys.exit(1)
        
    print(f"\nSuccessfully loaded {len(questions)} questions.")
    
    target = args.after_q or input("Start AFTER which question ID (e.g., Q.939): ").strip()
    if not target.upper().startswith('Q.'): 
        match = re.search(r'\d+', target)
        target = f"Q.{match.group()}" if match else target

    idx = 0
    found_target = False
    for i, q in enumerate(questions):
        if q['id'].upper() == target.upper():
            idx = i + 1
            found_target = True
            break
            
    if not found_target and args.after_q:
        print(f"Warning: Question '{target}' not found. Starting from the beginning.")

    for i in range(idx, len(questions)):
        q = questions[i]
        print("\n" + "="*80 + "\n" + q['text'] + "\n" + "-"*80)
        
        while True:
            ans = input("Your answer (A/B/C/D): ").strip().upper()
            if ans in ['A', 'B', 'C', 'D']: break
            print("Invalid input.")
            
        if ans == q['correct']:
            print("\n[CORRECT]")
        else:
            print(f"\n[INCORRECT] Correct: {q['correct']}")
        print("\n--- Explanation ---")
        print(q['explanation'])
        print("\n\nCompleted " + str(i) + "/" + str(len(questions))) 

        if q['ttr']:
            print("\n" + "*"*80 + "\n--- THINGS TO REMEMBER ---")
            print(q['ttr'] + "\n" + "*"*80)

if __name__ == "__main__":
    main()