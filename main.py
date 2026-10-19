import os, sys, sqlite3, calendar, random
from datetime import date, datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

APP_NAME = 'Klinik Dienstplan'
BASE = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'dienstplan.db')

class Store:
    def __init__(self):
        self.db=sqlite3.connect(DB_PATH); self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT DEFAULT '', weekly_hours REAL DEFAULT 38.5, max_days INTEGER DEFAULT 5, active INTEGER DEFAULT 1, notes TEXT DEFAULT '', preferences TEXT DEFAULT '', unavailable TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS shifts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, hours REAL NOT NULL, color TEXT DEFAULT '#4f46e5', coverage INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS eligibility(employee_id INTEGER, shift_id INTEGER, PRIMARY KEY(employee_id,shift_id), FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE, FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY, day TEXT NOT NULL, employee_id INTEGER NOT NULL, shift_id INTEGER, UNIQUE(day,employee_id), FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE, FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE SET NULL);
        ''')
        if self.db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0]==0:
            self.db.executemany('INSERT INTO shifts(name,start,end,hours,color,coverage) VALUES(?,?,?,?,?,?)',[
              ('Frühdienst','06:00','14:00',8,'#f59e0b',1),('Spätdienst','14:00','22:00',8,'#6366f1',1),('Nachtdienst','22:00','06:00',8,'#334155',1)])
        self.db.commit()
    def all(self,sql,args=()): return self.db.execute(sql,args).fetchall()
    def run(self,sql,args=()): self.db.execute(sql,args); self.db.commit()

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.store=Store(); self.title(APP_NAME); self.geometry('1280x800'); self.minsize(980,620)
        self.configure(bg='#f3f5f9'); self.style=ttk.Style(self); self.style.theme_use('clam')
        self.style.configure('TFrame',background='#f3f5f9'); self.style.configure('TLabel',background='#f3f5f9',foreground='#172033',font=('Segoe UI',10))
        self.style.configure('Title.TLabel',font=('Segoe UI',21,'bold'),foreground='#111827'); self.style.configure('Sub.TLabel',foreground='#64748b')
        self.style.configure('TButton',font=('Segoe UI',10),padding=(10,7)); self.style.configure('Accent.TButton',background='#4f46e5',foreground='white'); self.style.map('Accent.TButton',background=[('active','#4338ca')])
        self.style.configure('Treeview',font=('Segoe UI',10),rowheight=34,background='white',fieldbackground='white'); self.style.configure('Treeview.Heading',font=('Segoe UI',9,'bold'),background='#e9edf5')
        self.month=date.today().replace(day=1); self.tabs=ttk.Notebook(self); self.tabs.pack(fill='both',expand=True,padx=18,pady=14)
        self.plan_tab=ttk.Frame(self.tabs,padding=18); self.emp_tab=ttk.Frame(self.tabs,padding=18); self.shift_tab=ttk.Frame(self.tabs,padding=18)
        self.tabs.add(self.plan_tab,text='  Dienstplan  '); self.tabs.add(self.emp_tab,text='  Mitarbeitende  '); self.tabs.add(self.shift_tab,text='  Schichtmodelle  ')
        self.build_plan(); self.build_employees(); self.build_shifts()
    def header(self,parent,title,sub):
        ttk.Label(parent,text=title,style='Title.TLabel').pack(anchor='w'); ttk.Label(parent,text=sub,style='Sub.TLabel').pack(anchor='w',pady=(3,16))
    def build_plan(self):
        p=self.plan_tab; self.header(p,'Dienstplan','Monatsübersicht · Mitarbeitende und Schichten flexibel verwalten')
        bar=ttk.Frame(p); bar.pack(fill='x',pady=(0,12))
        ttk.Button(bar,text='‹',command=lambda:self.shift_month(-1)).pack(side='left'); self.month_lbl=ttk.Label(bar,text='',font=('Segoe UI',14,'bold')); self.month_lbl.pack(side='left',padx=12); ttk.Button(bar,text='›',command=lambda:self.shift_month(1)).pack(side='left')
        ttk.Button(bar,text='Plan automatisch erstellen',style='Accent.TButton',command=self.generate).pack(side='right',padx=(8,0)); ttk.Button(bar,text='CSV exportieren',command=self.export_csv).pack(side='right'); ttk.Button(bar,text='Plan leeren',command=self.clear_month).pack(side='right',padx=8)
        info=ttk.Label(p,text='Tipp: Klicke auf ein Feld, um die Schicht an diesem Tag zu ändern. Wünsche und Abwesenheiten werden beim Erstellen berücksichtigt.',style='Sub.TLabel'); info.pack(anchor='w',pady=(0,8))
        wrap=ttk.Frame(p); wrap.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(wrap,show='headings'); sy=ttk.Scrollbar(wrap,orient='vertical',command=self.tree.yview); sx=ttk.Scrollbar(wrap,orient='horizontal',command=self.tree.xview); self.tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        self.tree.grid(row=0,column=0,sticky='nsew'); sy.grid(row=0,column=1,sticky='ns'); sx.grid(row=1,column=0,sticky='ew'); wrap.rowconfigure(0,weight=1); wrap.columnconfigure(0,weight=1); self.tree.bind('<Double-1>',self.edit_cell); self.refresh_plan()
    def shift_month(self,n):
        y,m=self.month.year,self.month.month+n
        if m<1:y,m=y-1,12
        if m>12:y,m=y+1,1
        self.month=date(y,m,1); self.refresh_plan()
    def refresh_plan(self):
        self.month_lbl.config(text=f'{["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"][self.month.month-1]} {self.month.year}')
        emps=self.store.all('SELECT * FROM employees WHERE active=1 ORDER BY name'); days=calendar.monthrange(self.month.year,self.month.month)[1]
        cols=['employee']+[str(i) for i in range(1,days+1)]; self.tree['columns']=cols
        self.tree.heading('employee',text='MITARBEITER'); self.tree.column('employee',width=190,minwidth=160,stretch=False)
        for i in range(1,days+1):
            d=date(self.month.year,self.month.month,i); self.tree.heading(str(i),text=f'{["MO","DI","MI","DO","FR","SA","SO"][d.weekday()]}\n{i:02d}'); self.tree.column(str(i),width=64,minwidth=56,anchor='center',stretch=False)
        self.tree.delete(*self.tree.get_children()); shifts={r['id']:r for r in self.store.all('SELECT * FROM shifts')}
        assignments={(r['employee_id'],int(r['day'][-2:])):r['shift_id'] for r in self.store.all('SELECT * FROM assignments WHERE day LIKE ?', (self.month.strftime('%Y-%m')+'-%',))}
        for e in emps:
            vals=[e['name']+(f" · {e['role']}" if e['role'] else '')]
            for day in range(1,days+1):
                sid=assignments.get((e['id'],day)); vals.append(shifts[sid]['name'][:3] if sid and sid in shifts else '')
            self.tree.insert('', 'end',iid=str(e['id']),values=vals)
    def edit_cell(self,event):
        item=self.tree.identify_row(event.y); col=self.tree.identify_column(event.x)
        if not item or col=='#1':return
        day=int(col[1:]); d=date(self.month.year,self.month.month,day).isoformat(); eid=int(item)
        shifts=self.store.all('SELECT * FROM shifts ORDER BY start'); win=tk.Toplevel(self); win.title(f'Schicht am {day:02d}.{self.month.month:02d}.{self.month.year}'); win.resizable(False,False); win.transient(self); win.grab_set(); f=ttk.Frame(win,padding=18); f.pack(fill='both',expand=True)
        ttk.Label(f,text=f'{self.tree.item(item,"values")[0]} · {day:02d}.{self.month.month:02d}.{self.month.year}',font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(0,12))
        options={'Frei':None}; options.update({f"{s['name']} ({s['start']}–{s['end']})":s['id'] for s in shifts})
        current=self.store.all('SELECT shift_id FROM assignments WHERE day=? AND employee_id=?',(d,eid)); initial=next((k for k,v in options.items() if v==(current[0]['shift_id'] if current else None)),'Frei')
        choice=tk.StringVar(value=initial); ttk.Combobox(f,textvariable=choice,values=list(options),state='readonly',width=32).pack(fill='x',pady=6)
        def save():
            sid=options[choice.get()]
            if sid is None:self.store.run('DELETE FROM assignments WHERE day=? AND employee_id=?',(d,eid))
            else:self.store.run('INSERT INTO assignments(day,employee_id,shift_id) VALUES(?,?,?) ON CONFLICT(day,employee_id) DO UPDATE SET shift_id=excluded.shift_id',(d,eid,sid))
            win.destroy(); self.refresh_plan()
        ttk.Button(f,text='Speichern',style='Accent.TButton',command=save).pack(anchor='e',pady=(12,0))
    def generate(self):
        emps=self.store.all('SELECT * FROM employees WHERE active=1'); shifts=self.store.all('SELECT * FROM shifts ORDER BY start')
        if not emps or not shifts: messagebox.showinfo('Dienstplan','Lege zuerst Mitarbeitende und Schichtmodelle an.'); return
        if not messagebox.askyesno('Plan erstellen',f'Der Plan für {self.month.strftime("%B %Y")} wird neu erstellt. Bestehende Einträge dieses Monats werden ersetzt. Fortfahren?'):return
        start=self.month; days=calendar.monthrange(start.year,start.month)[1]; month_start=start.isoformat(); month_end=date(start.year,start.month,days).isoformat()
        self.store.run('DELETE FROM assignments WHERE day BETWEEN ? AND ?',(month_start,month_end))
        elig={e['id']:{r['shift_id'] for r in self.store.all('SELECT shift_id FROM eligibility WHERE employee_id=?',(e['id'],))} for e in emps}
        hist={e['id']:[] for e in emps}; assigned_hours={e['id']:0 for e in emps}; assigned_days={e['id']:set() for e in emps}
        for e in emps:
            for r in self.store.all('SELECT day,shift_id FROM assignments WHERE employee_id=? AND day<?',(e['id'],month_start)):
                hist[e['id']].append((r['day'],r['shift_id']))
        pref_map={e['id']:[x.strip() for x in (e['preferences'] or '').split(',') if x.strip()] for e in emps}
        blocked={e['id']:set(x.strip() for x in (e['unavailable'] or '').split(',') if x.strip()) for e in emps}
        for n in range(1,days+1):
            d=date(start.year,start.month,n); ds=d.isoformat()
            for sh in shifts:
                for _ in range(max(1,int(sh['coverage']))):
                    candidates=[]
                    for e in emps:
                        if ds in blocked[e['id']]: continue
                        if elig[e['id']] and sh['id'] not in elig[e['id']]: continue
                        if d.weekday()>=5 and 'Wochenende vermeiden' in pref_map[e['id']]:continue
                        if any(day==ds for day,sid in hist[e['id']]):continue
                        week_start=(d-timedelta(days=d.weekday())).isoformat()
                        week_end=(d+timedelta(days=6-d.weekday())).isoformat()
                        days_this_week=sum(1 for assigned in assigned_days[e['id']] if week_start<=assigned<=week_end)
                        if days_this_week >= int(e['max_days'] or 5):continue
                        # avoid more than five consecutive days
                        if all((d-timedelta(days=k)).isoformat() in assigned_days[e['id']] for k in range(1,6)):continue
                        # prevent night-to-early turnarounds
                        previous=[(day,sid) for day,sid in hist[e['id']] if day== (d-timedelta(days=1)).isoformat()]
                        if previous and 'Nacht' in next((x['name'] for x in shifts if x['id']==previous[-1][1]),'') and ('Früh' in sh['name'] or sh['start']<'09:00'):continue
                        weekly_target=float(e['weekly_hours'] or 38.5)*4.345
                        load=assigned_hours[e['id']]/max(1,weekly_target)
                        # Lower score is preferred; explicit shift/day preferences improve the score.
                        wish=sum(1 for x in pref_map[e['id']] if x.lower() in sh['name'].lower())
                        score=load*100+len(assigned_days[e['id']])*1.4+random.random()*7-wish*18
                        candidates.append((score,e))
                    if candidates:
                        e=min(candidates,key=lambda x:x[0])[1]
                        self.store.run('INSERT OR REPLACE INTO assignments(day,employee_id,shift_id) VALUES(?,?,?)',(ds,e['id'],sh['id']))
                        assigned_hours[e['id']]+=float(sh['hours']); assigned_days[e['id']].add(ds); hist[e['id']].append((ds,sh['id']))
        self.refresh_plan(); messagebox.showinfo('Dienstplan erstellt','Der Monatsplan wurde erstellt. Bitte prüfe Besetzung, Ruhezeiten und individuelle Wünsche vor der Freigabe.')
    def clear_month(self):
        if messagebox.askyesno('Plan leeren','Alle Schichteinträge in diesem Monat löschen?'):
            self.store.run('DELETE FROM assignments WHERE day LIKE ?',(self.month.strftime('%Y-%m')+'-%',)); self.refresh_plan()
    def export_csv(self):
        from tkinter.filedialog import asksaveasfilename
        import csv
        path=asksaveasfilename(defaultextension='.csv',initialfile=f'Dienstplan_{self.month:%Y_%m}.csv',filetypes=[('CSV-Datei','*.csv')])
        if not path:return
        rows=self.store.all('SELECT a.day,e.name,e.role,s.name AS shift,s.start,s.end,s.hours FROM assignments a JOIN employees e ON e.id=a.employee_id JOIN shifts s ON s.id=a.shift_id WHERE a.day LIKE ? ORDER BY a.day,e.name',(self.month.strftime('%Y-%m')+'-%',))
        with open(path,'w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f,delimiter=';'); w.writerow(['Datum','Mitarbeiter','Bereich','Schicht','Beginn','Ende','Stunden'])
            for r in rows:w.writerow(list(r))
        messagebox.showinfo('Export fertig',f'Der Plan wurde exportiert:\n{path}')
    def build_employees(self):
        p=self.emp_tab; self.header(p,'Mitarbeitende','Jede Person kann eigene Stunden, Einsatzbereiche, Wünsche und Abwesenheiten erhalten.')
        bar=ttk.Frame(p);bar.pack(fill='x',pady=(0,10));ttk.Button(bar,text='＋ Mitarbeiter hinzufügen',style='Accent.TButton',command=lambda:self.employee_form()).pack(side='left');ttk.Button(bar,text='Bearbeiten',command=self.edit_employee).pack(side='left',padx=8);ttk.Button(bar,text='Deaktivieren',command=self.deactivate_employee).pack(side='left')
        self.emp_tree=ttk.Treeview(p,columns=('name','role','hours','days','prefs','active'),show='headings');
        for c,t,w in [('name','Name',190),('role','Bereich / Qualifikation',190),('hours','Wochenstunden',110),('days','Max. Tage/Woche',120),('prefs','Wünsche',300),('active','Status',90)]:self.emp_tree.heading(c,text=t);self.emp_tree.column(c,width=w,anchor='w')
        self.emp_tree.pack(fill='both',expand=True); self.refresh_employees()
    def refresh_employees(self):
        if not hasattr(self,'emp_tree'):return
        self.emp_tree.delete(*self.emp_tree.get_children())
        for e in self.store.all('SELECT * FROM employees ORDER BY active DESC,name'):
            self.emp_tree.insert('','end',iid=str(e['id']),values=(e['name'],e['role'],e['weekly_hours'],e['max_days'],e['preferences'], 'Aktiv' if e['active'] else 'Inaktiv'))
        self.refresh_plan()
    def employee_form(self,e=None):
        win=tk.Toplevel(self);win.title('Mitarbeiter bearbeiten' if e else 'Mitarbeiter hinzufügen');win.transient(self);win.grab_set();f=ttk.Frame(win,padding=18);f.pack(fill='both',expand=True)
        vars={k:tk.StringVar(value=str(e[k] if e else default)) for k,default in [('name',''),('role',''),('weekly_hours','38.5'),('max_days','5'),('preferences',''),('unavailable','')]}
        labels=[('name','Name'),('role','Bereich / Qualifikation'),('weekly_hours','Vertragsstunden pro Woche'),('max_days','Max. Arbeitstage pro Woche'),('preferences','Wünsche (z. B. Frühdienst bevorzugt, Wochenende vermeiden)'),('unavailable','Abwesenheitstage (YYYY-MM-DD, kommasepariert)')]
        for i,(k,label) in enumerate(labels):ttk.Label(f,text=label).grid(row=i,column=0,sticky='w',pady=6);ttk.Entry(f,textvariable=vars[k],width=56).grid(row=i,column=1,sticky='ew',pady=6)
        ttk.Label(f,text='Einsetzbare Schichten').grid(row=6,column=0,sticky='nw',pady=8)
        shifts=self.store.all('SELECT * FROM shifts ORDER BY start'); selected={r['shift_id'] for r in self.store.all('SELECT shift_id FROM eligibility WHERE employee_id=?',(e['id'],))} if e else set(); checks=[]
        box=ttk.Frame(f);box.grid(row=6,column=1,sticky='w')
        for s in shifts:v=tk.BooleanVar(value=(not selected or s['id'] in selected));ttk.Checkbutton(box,text=s['name'],variable=v).pack(anchor='w');checks.append((s['id'],v))
        def save():
            if not vars['name'].get().strip():messagebox.showerror('Fehler','Bitte einen Namen eingeben.',parent=win);return
            try:hours=float(vars['weekly_hours'].get()); maxdays=int(vars['max_days'].get())
            except ValueError:messagebox.showerror('Fehler','Stunden und Arbeitstage müssen Zahlen sein.',parent=win);return
            vals=[vars[k].get().strip() for k in ('name','role')]+[hours,maxdays,vars['preferences'].get().strip(),vars['unavailable'].get().strip()]
            if e:
                self.store.run('UPDATE employees SET name=?,role=?,weekly_hours=?,max_days=?,preferences=?,unavailable=? WHERE id=?',(*vals,e['id']));eid=e['id']
            else:self.store.run('INSERT INTO employees(name,role,weekly_hours,max_days,preferences,unavailable) VALUES(?,?,?,?,?,?)',vals);eid=self.store.db.execute('SELECT last_insert_rowid()').fetchone()[0];self.store.db.commit()
            self.store.run('DELETE FROM eligibility WHERE employee_id=?',(eid,))
            for sid,v in checks:
                if v.get():self.store.run('INSERT INTO eligibility(employee_id,shift_id) VALUES(?,?)',(eid,sid))
            win.destroy();self.refresh_employees()
        ttk.Button(f,text='Speichern',style='Accent.TButton',command=save).grid(row=7,column=1,sticky='e',pady=(14,0)); f.columnconfigure(1,weight=1)
    def edit_employee(self):
        sel=self.emp_tree.selection()
        if not sel:messagebox.showinfo('Mitarbeitende','Bitte zuerst eine Person auswählen.');return
        e=self.store.all('SELECT * FROM employees WHERE id=?',(int(sel[0]),))[0];self.employee_form(e)
    def deactivate_employee(self):
        sel=self.emp_tree.selection()
        if not sel:return
        if messagebox.askyesno('Deaktivieren','Mitarbeiter deaktivieren? Die Plandaten bleiben erhalten.'):
            self.store.run('UPDATE employees SET active=0 WHERE id=?',(int(sel[0]),));self.refresh_employees()
    def build_shifts(self):
        p=self.shift_tab;self.header(p,'Schichtmodelle','Start, Ende, Stunden, Farbe und tägliche Mindestbesetzung festlegen.')
        top=ttk.Frame(p);top.pack(fill='x',pady=(0,10));ttk.Button(top,text='＋ Schicht hinzufügen',style='Accent.TButton',command=lambda:self.shift_form()).pack(side='left');ttk.Button(top,text='Bearbeiten',command=self.edit_shift).pack(side='left',padx=8);ttk.Button(top,text='Löschen',command=self.delete_shift).pack(side='left')
        self.shift_tree=ttk.Treeview(p,columns=('name','time','hours','coverage'),show='headings')
        for c,t,w in [('name','Schicht',220),('time','Arbeitszeit',220),('hours','Anrechenbare Stunden',180),('coverage','Mindestbesetzung pro Tag',220)]:self.shift_tree.heading(c,text=t);self.shift_tree.column(c,width=w,anchor='w')
        self.shift_tree.pack(fill='both',expand=True);self.refresh_shifts()
    def refresh_shifts(self):
        self.shift_tree.delete(*self.shift_tree.get_children())
        for s in self.store.all('SELECT * FROM shifts ORDER BY start'):self.shift_tree.insert('','end',iid=str(s['id']),values=(s['name'],f"{s['start']}–{s['end']}",s['hours'],s['coverage']))
    def shift_form(self,s=None):
        win=tk.Toplevel(self);win.title('Schichtmodell');win.transient(self);win.grab_set();f=ttk.Frame(win,padding=18);f.pack(fill='both',expand=True)
        data={'name':s['name'] if s else '', 'start':s['start'] if s else '06:00','end':s['end'] if s else '14:00','hours':str(s['hours'] if s else 8),'coverage':str(s['coverage'] if s else 1),'color':s['color'] if s else '#4f46e5'};vs={k:tk.StringVar(value=v) for k,v in data.items()}
        for i,(k,label) in enumerate([('name','Name'),('start','Beginn (HH:MM)'),('end','Ende (HH:MM)'),('hours','Stunden'),('coverage','Mindestbesetzung pro Tag'),('color','Farbe (Hex)')]):ttk.Label(f,text=label).grid(row=i,column=0,sticky='w',pady=6);ttk.Entry(f,textvariable=vs[k],width=30).grid(row=i,column=1,pady=6)
        def save():
            try:datetime.strptime(vs['start'].get(),'%H:%M');datetime.strptime(vs['end'].get(),'%H:%M');float(vs['hours'].get());int(vs['coverage'].get())
            except Exception:messagebox.showerror('Eingabe prüfen','Bitte Zeiten im Format HH:MM und gültige Zahlen eingeben.',parent=win);return
            values=(vs['name'].get().strip(),vs['start'].get(),vs['end'].get(),float(vs['hours'].get()),vs['color'].get(),int(vs['coverage'].get()))
            if s:self.store.run('UPDATE shifts SET name=?,start=?,end=?,hours=?,color=?,coverage=? WHERE id=?',(*values,s['id']))
            else:self.store.run('INSERT INTO shifts(name,start,end,hours,color,coverage) VALUES(?,?,?,?,?,?)',values)
            win.destroy();self.refresh_shifts();self.refresh_employees();self.refresh_plan()
        ttk.Button(f,text='Speichern',style='Accent.TButton',command=save).grid(row=6,column=1,sticky='e',pady=12)
    def edit_shift(self):
        sel=self.shift_tree.selection()
        if not sel:return
        self.shift_form(self.store.all('SELECT * FROM shifts WHERE id=?',(int(sel[0]),))[0])
    def delete_shift(self):
        sel=self.shift_tree.selection()
        if not sel:return
        if messagebox.askyesno('Löschen','Schichtmodell und dazugehörige Zuweisungen löschen?'):
            self.store.run('DELETE FROM shifts WHERE id=?',(int(sel[0]),));self.refresh_shifts();self.refresh_employees();self.refresh_plan()

if __name__=='__main__': App().mainloop()
