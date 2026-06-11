# Smart Form Fill 🧠📋

**Proforma → Editable Form → Auto-fill from Excel — for Indian teachers**

A free tool by [Rathore's Academy](https://rathoresacademy.wordpress.com) that takes any school proforma (report card, admit card, fee receipt, attendance sheet — image or PDF) and turns it into an editable, printable HTML form. Then upload your student Excel/CSV (any column format!) and let AI map the columns and auto-fill the form for any student — including automatic CBSE CGPA calculation.

## Features

1. **Upload proforma** (image/PDF) → Groq vision (`llama-4-scout`) reads the layout and generates a clean, editable HTML form
2. **Upload Excel/CSV** → any column naming convention works
3. **AI Column Mapping** → Groq fuzzy-matches your Excel columns to form fields (e.g. "Pupil Name" → "Student Name")
4. **Student dropdown** → select a student, form auto-fills instantly
5. **CBSE CGPA auto-calculation** (9-point scale, best 5 subjects)
6. **Print / Download** the filled form

## CBSE Grading Scale

| Marks  | Grade | Grade Point |
|--------|-------|-------------|
| 91–100 | A1    | 10          |
| 81–90  | A2    | 9           |
| 71–80  | B1    | 8           |
| 61–70  | B2    | 7           |
| 51–60  | C1    | 6           |
| 41–50  | C2    | 5           |
| 33–40  | D     | 4           |
| 21–32  | E1    | 3           |
| 0–20   | E2    | 2           |

CGPA = average of grade points of the **best 5 subjects**.

## Tech Stack

- **Backend:** Flask, Groq API (llama-4-scout vision), PyMuPDF (PDF→image), pandas/openpyxl (Excel/CSV)
- **Database:** PostgreSQL (conversion logging)
- **Deploy:** Render (GitHub auto-deploy)

## Local Setup

```bash
pip install -r requirements.txt
export GROQ_API_KEY=your_key_here
export DATABASE_URL=your_postgres_url   # optional, for logging
python app.py
```

Visit `http://localhost:5000`

## Environment Variables (Render)

- `GROQ_API_KEY` — your Groq API key
- `DATABASE_URL` — PostgreSQL connection string (optional)

## License

Free for educational use.
