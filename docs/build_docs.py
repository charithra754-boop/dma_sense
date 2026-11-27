"""Build Google-Docs-ready .docx versions of paper.tex and report.tex (LaTeX sources stay the master).

Steps: render each figs/*.tex to PNG (pdflatex + Ghostscript); flatten the LaTeX (generated tables
inlined, number macros expanded, figures -> PNG, algorithm -> list); number sections, figures, tables,
equations and citations ourselves (pandoc would leave raw labels); convert with pandoc to .docx.
Usage: /tmp/dv/bin/python docs/build_docs.py      (needs pypandoc_binary, pdflatex, gs)
Upload the .docx files to Google Drive and open them with Google Docs.
"""
import re
import subprocess
import zipfile
from pathlib import Path

import pypandoc

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "paper"
OUT = ROOT / "docs"
PNG = OUT / "figures"


# ---------- figures ----------
def render_figures():
    PNG.mkdir(parents=True, exist_ok=True)
    build = P / "build" / "figs"
    build.mkdir(parents=True, exist_ok=True)
    for f in sorted((P / "figs").glob("*.tex")):
        doc = (r"\documentclass{article}\usepackage[margin=1cm]{geometry}\pagestyle{empty}" "\n" r"\input{common}"
               "\n" r"\pgfplotsset{dmaplot/.append style={width=9cm,height=6.2cm}}"
               "\n" r"\begin{document}\input{figs/" + f.stem + r"}\end{document}" "\n")
        src = build / f"{f.stem}.tex"
        src.write_text(doc)
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={build}", str(src)],
                       cwd=P, check=True, capture_output=True)
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", "-r220", "-dTextAlphaBits=4",
                        "-dGraphicsAlphaBits=4", f"-sOutputFile={PNG / (f.stem + '.png')}", str(build / f"{f.stem}.pdf")],
                       check=True)
        subprocess.run(["magick", str(PNG / (f.stem + ".png")), "-trim", "-bordercolor", "white", "-border", "30",
                        str(PNG / (f.stem + ".png"))], check=True)


# ---------- helpers ----------
def numbers():
    m = {}
    for line in (P / "generated" / "numbers.tex").read_text().splitlines():
        r = re.match(r"\\newcommand\{\\(\w+)\}\{(.*?)\}\s*%", line)
        if r:
            m[r.group(1)] = r.group(2)
    return m


def bib():
    out = {}
    for line in (P / "refs.bib").read_text().splitlines():
        r = re.match(r"@(\w+)\{([^,]+),(.*)\}\s*$", line.strip())
        if not r:
            continue
        kind, key, body = r.groups()
        f = dict((k.lower(), v.replace("{", "").replace("}", "")) for k, v in
                 re.findall(r"(\w+)=\{((?:[^{}]|\{[^{}]*\})*)\}", body))
        parts = [f.get("author", ""), f"\u201c{f.get('title', '')},\u201d"]
        if "booktitle" in f:
            parts.append(f"in {f['booktitle']},")
        if "journal" in f:
            parts.append(f"{f['journal']},")
        if "volume" in f:
            parts.append(f"vol. {f['volume']},")
        if "number" in f:
            parts.append(f"no. {f['number']},")
        if "pages" in f:
            parts.append(f"pp. {f['pages'].replace('--', '-')},")
        if "note" in f:
            parts.append(f"{f['note']},")
        parts.append(f"{f.get('year', '')}.")
        out[key] = " ".join(p for p in parts if p)
    return out


def inline_inputs(s):
    s = re.sub(r"\\csname @@input\\endcsname (\S+)\s",
               lambda m: (P / m.group(1)).read_text() + "\n", s)
    return s


def strip_braced(s, cmd):
    """Remove a wrapper like \\resizebox{a}{b}{BODY} keeping BODY."""
    while True:
        i = s.find(cmd)
        if i < 0:
            return s
        j = i + len(cmd)
        # skip the fixed leading arguments in cmd (they are included in cmd); BODY starts at '{'
        assert s[j] == "{", s[i:i + 60]
        depth, k = 0, j
        while True:
            if s[k] == "{":
                depth += 1
            elif s[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        s = s[:i] + s[j + 1:k] + s[k + 1:]


def algorithm_to_list(s):
    def conv(m):
        cap = re.search(r"\\caption\{(.*?)\}", m.group(0)).group(1)
        body = re.search(r"\\begin\{algorithmic\}\[\d\](.*?)\\end\{algorithmic\}", m.group(0), re.S).group(1)
        items = []
        for line in body.strip().splitlines():
            t = line.strip()
            t = re.sub(r"\\State\s*", "", t)
            t = re.sub(r"\\While\{(.*)\}", r"\\textbf{while} \1 \\textbf{do}", t)
            t = t.replace(r"\EndWhile", r"\textbf{end while}")
            t = re.sub(r"\\If\{(.*?)\}", r"\\textbf{if} \1 \\textbf{then}", t)
            t = t.replace(r"\EndIf", r"\textbf{end if}")
            if t:
                items.append(r"\item " + t)
        return r"\textbf{" + cap + "}\n" + r"\begin{enumerate}" + "\n" + "\n".join(items) + "\n" + r"\end{enumerate}"
    return re.sub(r"\\begin\{algorithm\}.*?\\end\{algorithm\}", conv, s, flags=re.S)


def number_everything(s, appendix_letters=True):
    refs = {}
    # sections
    sec = sub = 0
    app = False
    out = []
    pos = 0
    for m in re.finditer(r"\\appendix|\\(section|subsection)\*?\{((?:[^{}]|\{[^{}]*\})*)\}(\\label\{([^}]+)\})?", s):
        out.append(s[pos:m.start()])
        pos = m.end()
        if m.group(0) == r"\appendix":
            app, sec = True, 0
            continue
        title, lab = m.group(2), m.group(4)
        if m.group(1) == "section":
            sec, sub = sec + 1, 0
            num = chr(64 + sec) if app and appendix_letters else str(sec)
            refs_num = num
            out.append(rf"\section*{{{num}. {title}}}" if not app else rf"\section*{{Appendix {num}. {title}}}")
        else:
            sub += 1
            num = (chr(64 + sec) if app else str(sec)) + f".{sub}"
            refs_num = num
            out.append(rf"\subsection*{{{num} {title}}}")
        if lab:
            refs[lab] = refs_num
    out.append(s[pos:])
    s = "".join(out)
    # figures and tables
    counters = {"figure": 0, "table": 0}

    def env(m):
        kind = "figure" if m.group(1).startswith("figure") else "table"
        counters[kind] += 1
        n = counters[kind]
        body = m.group(0)
        lab = re.search(r"\\label\{([^}]+)\}", body)
        if lab:
            refs[lab.group(1)] = str(n)
        prefix = f"Fig. {n}. " if kind == "figure" else f"Table {n}. "
        body = re.sub(r"\\caption\{", lambda _: r"\caption{" + prefix, body, count=1)
        return body
    s = re.sub(r"\\begin\{(figure\*?|table\*?)\}.*?\\end\{\1\}", env, s, flags=re.S)
    # equations: number equation envs and align lines (no \nonumber in our sources)
    eq = [0]

    def eqenv(m):
        name, body = m.group(1), m.group(2)
        if name == "equation":
            eq[0] += 1
            lab = re.search(r"\\label\{([^}]+)\}", body)
            if lab:
                refs[lab.group(1)] = str(eq[0])
            body = re.sub(r"\\label\{[^}]+\}", "", body).rstrip()
            return rf"\begin{{equation}}{body}\qquad ({eq[0]})\end{{equation}}"
        lines = re.split(r"\\\\", body)
        new = []
        for ln in lines:
            if not ln.strip():
                continue
            eq[0] += 1
            lab = re.search(r"\\label\{([^}]+)\}", ln)
            if lab:
                refs[lab.group(1)] = str(eq[0])
            ln = re.sub(r"\\label\{[^}]+\}", "", ln).rstrip()
            new.append(ln + rf"\qquad ({eq[0]})")
        return r"\begin{align}" + r"\\".join(new) + r"\end{align}"
    s = re.sub(r"\\begin\{(equation|align)\}(.*?)\\end\{\1\}", eqenv, s, flags=re.S)
    s = re.sub(r"\\label\{[^}]+\}", "", s)
    s = re.sub(r"\\ref\{([^}]+)\}", lambda m: refs.get(m.group(1), "?"), s)
    return s


def citations(s, bibmap):
    order = []

    def cite(m):
        nums = []
        for k in m.group(1).split(","):
            k = k.strip()
            if k not in order:
                order.append(k)
            nums.append(f"[{order.index(k) + 1}]")
        return ", ".join(nums)
    s = re.sub(r"\\cite\{([^}]+)\}", cite, s)
    refs = "\n".join(rf"\item[{{[{i + 1}]}}] {bibmap[k]}" for i, k in enumerate(order))
    block = r"\section*{References}" + "\n" + r"\begin{description}" + "\n" + refs + "\n" + r"\end{description}"
    return re.sub(r"\\bibliographystyle\{\w+\}\s*\\bibliography\{\w+\}", lambda _: block, s)


def flatten(name):
    s = (P / f"{name}.tex").read_text()
    s = re.sub(r"(?<!\\)%.*", "", s)
    s = inline_inputs(s)
    nums = numbers()
    for k in sorted(nums, key=len, reverse=True):
        s = re.sub(r"\\" + k + r"(\{\})?(?![A-Za-z])", lambda _m, v=nums[k]: v, s)
    s = s.replace(r"$\imu>\npu>\cam$", "IMU > NPU > CAM")
    s = re.sub(r"\\pone(\{\})?", r"$p_{99}$", s)
    s = s.replace("$$p_{99}$$", "$p_{99}$")
    s = re.sub(r"\\(imu|npu|cam)\b", lambda m: m.group(1).upper(), s)
    s = s.replace(r"\illusnote", "").replace(r"\textsc{Arbitrate}", r"\mathrm{Arbitrate}")
    s = re.sub(r"\\input\{figs/(\w+)\}",
               lambda m: rf"\includegraphics[width=9cm]{{{PNG / (m.group(1) + '.png')}}}", s)
    s = strip_braced(s, r"\resizebox{\linewidth}{!}")
    s = algorithm_to_list(s)
    s = s.replace(r"\tableofcontents\newpage", "").replace(r"\tableofcontents", "")
    s = re.sub(r"\\begin\{(table|figure)\*\}", r"\\begin{\1}", s).replace(r"\end{table*}", r"\end{table}")
    # math tweaks pandoc dislikes
    s = s.replace(r"\!", "")
    # IEEE front matter -> plain
    s = re.sub(r"\\begin\{IEEEkeywords\}(.*?)\\end\{IEEEkeywords\}", r"\\noindent\\textbf{Keywords:} \1", s, flags=re.S)
    s = re.sub(r"\\author\{.*?\}\s*\\maketitle", "", s, flags=re.S) if "IEEEauthorblock" in s else s
    s = number_everything(s)
    s = citations(s, bib())
    pre = s[:s.index(r"\begin{document}")]
    body = s[s.index(r"\begin{document}"):]
    title = re.search(r"\\title\{(.*?)\}\s*\n", pre, re.S)
    t = title.group(1) if title else ""
    t = re.sub(r"\\\\(\[[^\]]*\])?", " ", t).replace(r"\large", "")
    header = (r"\documentclass{article}\usepackage{amsmath,graphicx}" "\n" r"\title{" + t + "}\n"
              r"\author{Ahad Ulla Baig, Divisha Gera and Charithra G\\Dept. of Artificial Intelligence and Machine Learning, "
              r"Ramaiah Institute of Technology, Bengaluru, India}" "\n" r"\date{}" "\n")
    body = body.replace(r"\maketitle", "")
    body = body.replace(r"\begin{document}", r"\begin{document}\maketitle", 1)
    return header + body


def reference_docx():
    ref = OUT / "reference.docx"
    subprocess.run([pypandoc.get_pandoc_path(), "-o", str(ref), "--print-default-data-file", "reference.docx"],
                   check=True)
    # body and headings in Times New Roman (available in Google Docs)
    tmp = ref.with_suffix(".tmp")
    with zipfile.ZipFile(ref) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            b = zin.read(item.filename)
            if item.filename == "word/styles.xml":   # 11 pt body (was 12), tighter paragraph spacing
                b = b.replace(b'<w:sz w:val="24" />\n        <w:szCs w:val="24" />', b'<w:sz w:val="22" />\n        <w:szCs w:val="22" />', 1)
                b = b.replace(b'<w:spacing w:after="200" />', b'<w:spacing w:after="120" />', 1)
            if item.filename == "word/theme/theme1.xml":
                b = re.sub(rb'(<a:(?:latin) typeface=")[^"]*(")', rb"\1Times New Roman\2", b)
            zout.writestr(item, b)
    tmp.replace(ref)
    return ref


def main():
    OUT.mkdir(exist_ok=True)
    render_figures()
    ref = reference_docx()
    for name, outname in (("paper", "DMA-Sense_IEEE_paper.docx"), ("report", "DMA-Sense_project_report.docx")):
        flat = flatten(name)
        (P / "build" / f"{name}_flat.tex").write_text(flat)
        pypandoc.convert_text(flat, "docx", format="latex", outputfile=str(OUT / outname),
                              extra_args=[f"--reference-doc={ref}", f"--resource-path={P}"])
        print("wrote", OUT / outname)


if __name__ == "__main__":
    main()
