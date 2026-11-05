"""Kleines Testfenster: Volltext eingeben und Titel, Beschreibung und Keywords erzeugen lassen.

    python training/demo_ui.py
    python training/demo_ui.py --model runs/mt5-meta/final --device cuda --beams 4

Fünf Mustertexte aus dem Testdatensatz lassen sich per Knopf laden; die Referenzmetadaten des Datensatzes stehen
darunter, sodass die Ausgabe direkt verglichen werden kann. Freie Eingabe ist genauso möglich.

Die Erzeugung läuft in einem Hintergrund-Thread, damit das Fenster während der Generierung nicht einfriert. Modell,
Eingabemodus und Längen kommen aus ``metadata_generation.json`` im Modellordner – also genau wie bei ``predict.py``.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textprep import TASKS, prepare_input  # noqa: E402

HERE = Path(__file__).resolve().parent
FIELD_NAMES = {"title": "Titel", "description": "Beschreibung", "keywords": "Keywords"}


class MetadataModel:
    """Hält Tokenizer und Modell; erzeugt die Felder einzeln nacheinander."""

    def __init__(self, model_path: str, device: str, num_beams: int):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.torch = torch
        settings_file = Path(model_path) / "metadata_generation.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
        self.tasks = settings.get("tasks", TASKS)
        self.lengths = settings.get("max_target_lengths", {"title": 48, "description": 256, "keywords": 96})
        self.max_source = settings.get("max_source_length", 1024)
        self.mode = settings.get("input_mode", "head")
        self.num_beams = num_beams
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.tok = AutoTokenizer.from_pretrained(model_path)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_path)
        if device == "cuda" and torch.cuda.is_bf16_supported():
            model = model.to(torch.bfloat16)  # bf16 wie im Training; fp16 ist mit mT5 instabil
        self.model = model.to(device).eval()

    def count_tokens(self, text: str) -> int:
        return len(self.tok(prepare_input(text, self.mode)).input_ids)

    def generate(self, text: str, fields: list[str], on_field=None) -> dict[str, tuple[str, float]]:
        source = prepare_input(text, self.mode)
        results: dict[str, tuple[str, float]] = {}
        for field in fields:
            start = time.perf_counter()
            enc = self.tok(self.tasks[field] + source, max_length=self.max_source, truncation=True,
                           return_tensors="pt").to(self.device)
            with self.torch.no_grad():
                ids = self.model.generate(**enc, max_new_tokens=self.lengths.get(field, 128),
                                          num_beams=self.num_beams, no_repeat_ngram_size=3)
            results[field] = (self.tok.decode(ids[0], skip_special_tokens=True), time.perf_counter() - start)
            if on_field:
                on_field(field, *results[field])
        return results


class App:
    def __init__(self, root: tk.Tk, model_path: str, device: str, beams: int, samples_file: Path):
        self.root = root
        self.model: MetadataModel | None = None
        self.load_error: str | None = None
        self.events: queue.Queue = queue.Queue()
        self.samples = self._load_samples(samples_file)
        self.reference: dict[str, str] = {}
        self.current_sample: int | None = None
        self.buttons: list[tk.Widget] = []  # alles, was während einer Erzeugung gesperrt wird

        root.title("Metadaten-Test – mT5")
        root.geometry("920x860")
        root.minsize(760, 640)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=3)
        root.rowconfigure(6, weight=2)

        self._build_samples()
        self._build_input()
        self._build_options()
        self._build_outputs()
        self._build_references()

        self.status = tk.StringVar(value="Modell wird geladen …")
        ttk.Label(root, textvariable=self.status, anchor="w").grid(
            row=5, column=0, sticky="ew", padx=10, pady=(4, 2))

        threading.Thread(target=self._load_model, args=(model_path, device, beams), daemon=True).start()
        self.root.after(100, self._drain)

    # ---------------------------------------------------------------- Aufbau

    def _load_samples(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _build_samples(self):
        frame = ttk.LabelFrame(self.root, text="Mustertexte")
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        for i, sample in enumerate(self.samples[:5]):
            btn = ttk.Button(frame, text=f"Muster {i + 1}", width=12,
                             command=lambda n=i: self._use_sample(n))
            btn.grid(row=0, column=i, padx=(6, 0), pady=6, sticky="w")
            self.buttons.append(btn)
        ttk.Button(frame, text="Eingabe leeren", command=self._clear).grid(
            row=0, column=5, padx=(18, 6), pady=6, sticky="w")
        self.sample_hint = ttk.Label(frame, text="oder Text unten frei eingeben", foreground="#666")
        self.sample_hint.grid(row=0, column=6, padx=(6, 6), sticky="w")

    def _build_input(self):
        ttk.Label(self.root, text="Volltext (Markdown oder reiner Text):").grid(
            row=1, column=0, sticky="w", padx=10)
        self.input = ScrolledText(self.root, height=14, wrap="word", undo=True,
                                  font=("Segoe UI", 10))
        self.input.grid(row=2, column=0, sticky="nsew", padx=10, pady=(2, 0))
        self.input.bind("<<Modified>>", self._input_changed)
        self.input.edit_modified(False)
        self.buttons.append(self.input)
        self.info = tk.StringVar(value="0 Zeichen")
        ttk.Label(self.root, textvariable=self.info, foreground="#666").grid(
            row=3, column=0, sticky="w", padx=10, pady=(2, 4))

    def _build_options(self):
        frame = ttk.Frame(self.root)
        frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 2))
        self.checks: dict[str, tk.BooleanVar] = {}
        for i, field in enumerate(("title", "description", "keywords")):
            var = tk.BooleanVar(value=True)
            self.checks[field] = var
            ttk.Checkbutton(frame, text=FIELD_NAMES[field], variable=var).grid(
                row=0, column=i, padx=(0, 12), sticky="w")
        ttk.Label(frame, text="Beams:").grid(row=0, column=3, padx=(12, 4))
        self.beams = tk.IntVar(value=4)
        ttk.Spinbox(frame, from_=1, to=8, width=4, textvariable=self.beams).grid(row=0, column=4)
        self.run_button = ttk.Button(frame, text="Erzeugen", command=self._run_checked)
        self.run_button.grid(row=0, column=5, padx=(16, 0))
        self.buttons.append(self.run_button)
        for i, field in enumerate(("title", "description", "keywords")):
            btn = ttk.Button(frame, text=f"nur {FIELD_NAMES[field]}",
                             command=lambda f=field: self._run_only(f))
            btn.grid(row=0, column=6 + i, padx=(6, 0))
            self.buttons.append(btn)
        ttk.Label(frame, foreground="#666", wraplength=860, justify="left",
                  text="Beams = parallele Kandidaten pro Token: 1 = gierig (am schnellsten, aber schwächer), "
                       "4 = Standard, 8 bringt bei diesem Modell kaum mehr. Der erste Lauf ist wegen "
                       "CUDA-Warmup langsamer.").grid(
            row=1, column=0, columnspan=9, sticky="w", pady=(6, 0))

    def _build_outputs(self):
        frame = ttk.LabelFrame(self.root, text="Erzeugt")
        frame.grid(row=6, column=0, sticky="nsew", padx=10, pady=4)
        frame.columnconfigure(1, weight=1)
        self.out_labels: dict[str, tk.StringVar] = {}
        self.out_texts: dict[str, tk.Text] = {}
        heights = {"title": 2, "description": 6, "keywords": 3}
        for row, field in enumerate(("title", "description", "keywords")):
            label = tk.StringVar(value=FIELD_NAMES[field])
            self.out_labels[field] = label
            ttk.Label(frame, textvariable=label, width=16, anchor="nw").grid(
                row=row, column=0, sticky="nw", padx=(8, 4), pady=4)
            text = tk.Text(frame, height=heights[field], wrap="word", font=("Segoe UI", 10),
                           state="disabled", background="#fafafa")
            text.grid(row=row, column=1, sticky="nsew", pady=4)
            frame.rowconfigure(row, weight=1 if field == "description" else 0)
            self.out_texts[field] = text
            ttk.Button(frame, text="Kopieren", width=9,
                       command=lambda f=field: self._copy(f)).grid(
                row=row, column=2, padx=8, pady=4, sticky="n")

    def _build_references(self):
        frame = ttk.LabelFrame(self.root, text="Referenz aus dem Testdatensatz")
        frame.grid(row=7, column=0, sticky="nsew", padx=10, pady=(4, 10))
        frame.columnconfigure(0, weight=1)
        self.reference_text = tk.Text(frame, height=5, wrap="word", font=("Segoe UI", 9),
                                      state="disabled", background="#f4f4f4", foreground="#444")
        self.reference_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=6)

    # ---------------------------------------------------------------- Aktionen

    def _use_sample(self, index: int):
        sample = self.samples[index]
        self.current_sample = index
        self._set_input(sample["fulltext"])
        self.reference = {f: sample[f] for f in ("title", "description", "keywords")}
        ref = "\n".join(f"{FIELD_NAMES[f]}: {self.reference[f]}" for f in ("title", "description", "keywords"))
        self._set_text(self.reference_text, ref)
        self.status.set(f"Muster {index + 1} geladen – Referenz steht unten.")
        self._update_info()

    def _clear(self):
        self.current_sample = None
        self.reference = {}
        self._set_input("")
        self._set_text(self.reference_text, "")
        for field, widget in self.out_texts.items():
            self._set_text(widget, "")
            self.out_labels[field].set(FIELD_NAMES[field])
        self.status.set("Bereit.")
        self._update_info()

    def _run_checked(self):
        fields = [f for f in ("title", "description", "keywords") if self.checks[f].get()]
        self._start(fields)

    def _run_only(self, field: str):
        for f, var in self.checks.items():
            var.set(f == field)
        self._start([field])

    def _start(self, fields: list[str]):
        if not fields:
            self.status.set("Bitte mindestens ein Feld auswählen.")
            return
        if self.model is None:
            self.status.set(self.load_error or "Modell wird noch geladen …")
            return
        text = self.input.get("1.0", "end-1c").strip()
        if not text:
            self.status.set("Bitte zuerst einen Volltext eingeben oder ein Muster laden.")
            return
        self.model.num_beams = self.beams.get()
        self._set_busy(True)
        for field in fields:
            self._set_text(self.out_texts[field], "")
            self.out_labels[field].set(FIELD_NAMES[field])
        self.status.set("Erzeuge " + ", ".join(FIELD_NAMES[f] for f in fields) + " …")
        threading.Thread(target=self._work, args=(text, fields), daemon=True).start()

    def _copy(self, field: str):
        value = self.out_texts[field].get("1.0", "end-1c").strip()
        if value:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.status.set(f"{FIELD_NAMES[field]} in die Zwischenablage kopiert.")

    # ---------------------------------------------------------------- Hintergrund

    def _load_model(self, path: str, device: str, beams: int):
        try:
            if not Path(path).is_dir():
                raise FileNotFoundError(f"Modellordner nicht gefunden: {path}")
            self.model = MetadataModel(path, device, beams)
        except Exception as exc:  # im Fenster anzeigen, nicht nur auf der Konsole
            self.load_error = f"Modell konnte nicht geladen werden: {exc}"
            print(self.load_error, file=sys.stderr)
            self.events.put(("load_error", self.load_error))
            return
        self.events.put(("loaded", self.model.device, self.model.max_source))

    def _work(self, text: str, fields: list[str]):
        try:
            results = self.model.generate(text, fields, lambda f, t, s: self.events.put(("field", f, t, s)))
        except Exception as exc:
            self.events.put(("error", str(exc)))
            return
        self.events.put(("done", tuple(results)))

    def _drain(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "loaded":
                    _, device, max_source = event
                    self.status.set(f"Modell geladen ({device}, Eingabe max. {max_source} Tokens). "
                                    f"Muster wählen oder Text einfügen.")
                elif kind == "load_error":
                    self.status.set(event[1])
                elif kind == "field":
                    _, field, text, seconds = event
                    self._set_text(self.out_texts[field], text)
                    self.out_labels[field].set(f"{FIELD_NAMES[field]} ({seconds:.1f} s)")
                elif kind == "done":
                    self._set_busy(False)
                    self.status.set("Fertig.")
                elif kind == "error":
                    self._set_busy(False)
                    self.status.set(f"Fehler: {event[1]}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    # ---------------------------------------------------------------- Hilfen

    def _set_busy(self, busy: bool):
        for widget in self.buttons:
            widget.configure(state="disabled" if busy else "normal")
        if busy:
            self.run_button.configure(state="disabled")

    def _set_input(self, text: str):
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)
        self.input.edit_modified(False)

    @staticmethod
    def _set_text(widget: tk.Text, value: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _input_changed(self, _event=None):
        if not self.input.edit_modified():
            return
        self.input.edit_modified(False)
        self._update_info()

    def _update_info(self):
        text = self.input.get("1.0", "end-1c").strip()
        if not text:
            self.info.set("0 Zeichen")
            return
        parts = [f"{len(text):,} Zeichen"]
        if self.model is not None:
            try:
                tokens = self.model.count_tokens(text)
                cut = " – wird gekürzt" if tokens > self.model.max_source else ""
                parts.append(f"{tokens:,} Tokens{cut}")
            except Exception:
                pass
        self.info.set(" · ".join(parts).replace(",", "."))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="runs/mt5-meta/final", help="Modellordner (default: runs/mt5-meta/final)")
    ap.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    ap.add_argument("--beams", type=int, default=4, help="Startwert für num_beams (default: 4)")
    ap.add_argument("--samples", type=Path, default=HERE / "demo_samples.json")
    a = ap.parse_args(argv)

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root, a.model, a.device, a.beams, a.samples)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
