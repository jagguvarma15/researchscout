// Display-only mirror of researchscout/taxonomy.py for rendering the filter controls and
// category badge tooltips. The server stays authoritative: if this list drifts, a stale subject
// is rejected with a 422 naming it and an unknown code renders without a name, never wrong data.
//
// Two axes, matching the API. A subject is the field a paper is in; a topic is the technique it
// uses. They are different questions, so they narrow each other rather than competing.

export interface SubjectOption {
  key: string;
  label: string;
  /** Whole archives this subject covers. */
  archives: string[];
  /** Individual category codes, for subjects that are not a whole archive. */
  categories: string[];
  /** Core subjects are what this radar is about; the rest are where it meets other fields. */
  core: boolean;
}

const PHYSICS_ARCHIVES = [
  'astro-ph',
  'cond-mat',
  'gr-qc',
  'hep-ex',
  'hep-lat',
  'hep-ph',
  'hep-th',
  'math-ph',
  'nlin',
  'nucl-ex',
  'nucl-th',
  'physics',
  'quant-ph',
];

export const SUBJECTS: SubjectOption[] = [
  {
    key: 'ai',
    label: 'AI and machine learning',
    archives: [],
    categories: [
      'cs.AI',
      'cs.CL',
      'cs.CV',
      'cs.IR',
      'cs.LG',
      'cs.MA',
      'cs.NE',
      'cs.RO',
      'eess.AS',
      'eess.IV',
      'stat.ML',
    ],
    core: true,
  },
  { key: 'stats', label: 'Statistics', archives: ['stat'], categories: [], core: true },
  {
    key: 'data',
    label: 'Data science',
    archives: [],
    categories: ['cs.DB', 'cs.DL', 'cs.DM', 'cs.DS', 'cs.IR', 'stat.AP', 'stat.CO'],
    core: true,
  },
  {
    key: 'math',
    label: 'Mathematics',
    archives: ['math', 'math-ph'],
    categories: ['cs.CC', 'cs.GT', 'cs.LO', 'cs.NA', 'cs.SC'],
    core: true,
  },
  { key: 'bio', label: 'Biology and health', archives: ['q-bio'], categories: [], core: false },
  {
    key: 'physical',
    label: 'Physical sciences',
    archives: PHYSICS_ARCHIVES,
    categories: [],
    core: false,
  },
  {
    key: 'security',
    label: 'Security and privacy',
    archives: [],
    categories: ['cs.CR'],
    core: false,
  },
  {
    key: 'society',
    label: 'Society and economics',
    archives: ['econ', 'q-fin'],
    categories: ['cs.CY', 'cs.HC', 'cs.SI'],
    core: false,
  },
  {
    key: 'systems',
    label: 'Systems and software',
    archives: [],
    categories: [
      'cs.AR',
      'cs.DC',
      'cs.NI',
      'cs.OS',
      'cs.PF',
      'cs.PL',
      'cs.SE',
      'cs.SY',
      'eess.SY',
    ],
    core: false,
  },
];

export function subjectLabel(key: string): string {
  return SUBJECTS.find((subject) => subject.key === key)?.label ?? key;
}

export interface TopicOption {
  key: string;
  label: string;
  /** Short form for the toolbar, where three of these sit side by side. */
  short: string;
}

// NLP and CV are arXiv categories; RL is a phrase match, because arXiv has no category for it.
// The server owns that distinction - here they are three equivalent buttons.
export const TOPICS: TopicOption[] = [
  { key: 'nlp', label: 'Natural language processing', short: 'NLP' },
  { key: 'cv', label: 'Computer vision', short: 'CV' },
  { key: 'rl', label: 'Reinforcement learning', short: 'RL' },
];

export function topicLabel(key: string): string {
  return TOPICS.find((topic) => topic.key === key)?.short ?? key;
}

// Full arXiv taxonomy (arxiv.org/category_taxonomy), keyed by category code. Used for badge
// tooltips everywhere and for the sidebar's per-category checklists (tech groups only).
export const CATEGORY_NAMES: Record<string, string> = {
  // Computer Science
  'cs.AI': 'Artificial Intelligence',
  'cs.AR': 'Hardware Architecture',
  'cs.CC': 'Computational Complexity',
  'cs.CE': 'Computational Engineering, Finance, and Science',
  'cs.CG': 'Computational Geometry',
  'cs.CL': 'Computation and Language',
  'cs.CR': 'Cryptography and Security',
  'cs.CV': 'Computer Vision and Pattern Recognition',
  'cs.CY': 'Computers and Society',
  'cs.DB': 'Databases',
  'cs.DC': 'Distributed, Parallel, and Cluster Computing',
  'cs.DL': 'Digital Libraries',
  'cs.DM': 'Discrete Mathematics',
  'cs.DS': 'Data Structures and Algorithms',
  'cs.ET': 'Emerging Technologies',
  'cs.FL': 'Formal Languages and Automata Theory',
  'cs.GL': 'General Literature',
  'cs.GR': 'Graphics',
  'cs.GT': 'Computer Science and Game Theory',
  'cs.HC': 'Human-Computer Interaction',
  'cs.IR': 'Information Retrieval',
  'cs.IT': 'Information Theory',
  'cs.LG': 'Machine Learning',
  'cs.LO': 'Logic in Computer Science',
  'cs.MA': 'Multiagent Systems',
  'cs.MM': 'Multimedia',
  'cs.MS': 'Mathematical Software',
  'cs.NA': 'Numerical Analysis',
  'cs.NE': 'Neural and Evolutionary Computing',
  'cs.NI': 'Networking and Internet Architecture',
  'cs.OH': 'Other Computer Science',
  'cs.OS': 'Operating Systems',
  'cs.PF': 'Performance',
  'cs.PL': 'Programming Languages',
  'cs.RO': 'Robotics',
  'cs.SC': 'Symbolic Computation',
  'cs.SD': 'Sound',
  'cs.SE': 'Software Engineering',
  'cs.SI': 'Social and Information Networks',
  'cs.SY': 'Systems and Control',
  // Statistics
  'stat.AP': 'Applications',
  'stat.CO': 'Computation',
  'stat.ME': 'Methodology',
  'stat.ML': 'Machine Learning',
  'stat.OT': 'Other Statistics',
  'stat.TH': 'Statistics Theory',
  // Electrical Engineering and Systems Science
  'eess.AS': 'Audio and Speech Processing',
  'eess.IV': 'Image and Video Processing',
  'eess.SP': 'Signal Processing',
  'eess.SY': 'Systems and Control',
  // Mathematics
  'math.AC': 'Commutative Algebra',
  'math.AG': 'Algebraic Geometry',
  'math.AP': 'Analysis of PDEs',
  'math.AT': 'Algebraic Topology',
  'math.CA': 'Classical Analysis and ODEs',
  'math.CO': 'Combinatorics',
  'math.CT': 'Category Theory',
  'math.CV': 'Complex Variables',
  'math.DG': 'Differential Geometry',
  'math.DS': 'Dynamical Systems',
  'math.FA': 'Functional Analysis',
  'math.GM': 'General Mathematics',
  'math.GN': 'General Topology',
  'math.GR': 'Group Theory',
  'math.GT': 'Geometric Topology',
  'math.HO': 'History and Overview',
  'math.IT': 'Information Theory',
  'math.KT': 'K-Theory and Homology',
  'math.LO': 'Logic',
  'math.MG': 'Metric Geometry',
  'math.MP': 'Mathematical Physics',
  'math.NA': 'Numerical Analysis',
  'math.NT': 'Number Theory',
  'math.OA': 'Operator Algebras',
  'math.OC': 'Optimization and Control',
  'math.PR': 'Probability',
  'math.QA': 'Quantum Algebra',
  'math.RA': 'Rings and Algebras',
  'math.RT': 'Representation Theory',
  'math.SG': 'Symplectic Geometry',
  'math.SP': 'Spectral Theory',
  'math.ST': 'Statistics Theory',
  // Economics
  'econ.EM': 'Econometrics',
  'econ.GN': 'General Economics',
  'econ.TH': 'Theoretical Economics',
  // Astrophysics
  'astro-ph.CO': 'Cosmology and Nongalactic Astrophysics',
  'astro-ph.EP': 'Earth and Planetary Astrophysics',
  'astro-ph.GA': 'Astrophysics of Galaxies',
  'astro-ph.HE': 'High Energy Astrophysical Phenomena',
  'astro-ph.IM': 'Instrumentation and Methods for Astrophysics',
  'astro-ph.SR': 'Solar and Stellar Astrophysics',
  // Condensed Matter
  'cond-mat.dis-nn': 'Disordered Systems and Neural Networks',
  'cond-mat.mes-hall': 'Mesoscale and Nanoscale Physics',
  'cond-mat.mtrl-sci': 'Materials Science',
  'cond-mat.other': 'Other Condensed Matter',
  'cond-mat.quant-gas': 'Quantum Gases',
  'cond-mat.soft': 'Soft Condensed Matter',
  'cond-mat.stat-mech': 'Statistical Mechanics',
  'cond-mat.str-el': 'Strongly Correlated Electrons',
  'cond-mat.supr-con': 'Superconductivity',
  // Nonlinear Sciences
  'nlin.AO': 'Adaptation and Self-Organizing Systems',
  'nlin.CD': 'Chaotic Dynamics',
  'nlin.CG': 'Cellular Automata and Lattice Gases',
  'nlin.PS': 'Pattern Formation and Solitons',
  'nlin.SI': 'Exactly Solvable and Integrable Systems',
  // Physics
  'physics.acc-ph': 'Accelerator Physics',
  'physics.ao-ph': 'Atmospheric and Oceanic Physics',
  'physics.app-ph': 'Applied Physics',
  'physics.atm-clus': 'Atomic and Molecular Clusters',
  'physics.atom-ph': 'Atomic Physics',
  'physics.bio-ph': 'Biological Physics',
  'physics.chem-ph': 'Chemical Physics',
  'physics.class-ph': 'Classical Physics',
  'physics.comp-ph': 'Computational Physics',
  'physics.data-an': 'Data Analysis, Statistics and Probability',
  'physics.ed-ph': 'Physics Education',
  'physics.flu-dyn': 'Fluid Dynamics',
  'physics.gen-ph': 'General Physics',
  'physics.geo-ph': 'Geophysics',
  'physics.hist-ph': 'History and Philosophy of Physics',
  'physics.ins-det': 'Instrumentation and Detectors',
  'physics.med-ph': 'Medical Physics',
  'physics.optics': 'Optics',
  'physics.plasm-ph': 'Plasma Physics',
  'physics.pop-ph': 'Popular Physics',
  'physics.soc-ph': 'Physics and Society',
  'physics.space-ph': 'Space Physics',
  // Quantitative Biology
  'q-bio.BM': 'Biomolecules',
  'q-bio.CB': 'Cell Behavior',
  'q-bio.GN': 'Genomics',
  'q-bio.MN': 'Molecular Networks',
  'q-bio.NC': 'Neurons and Cognition',
  'q-bio.OT': 'Other Quantitative Biology',
  'q-bio.PE': 'Populations and Evolution',
  'q-bio.QM': 'Quantitative Methods',
  'q-bio.SC': 'Subcellular Processes',
  'q-bio.TO': 'Tissues and Organs',
  // Quantitative Finance
  'q-fin.CP': 'Computational Finance',
  'q-fin.EC': 'Economics',
  'q-fin.GN': 'General Finance',
  'q-fin.MF': 'Mathematical Finance',
  'q-fin.PM': 'Portfolio Management',
  'q-fin.PR': 'Pricing of Securities',
  'q-fin.RM': 'Risk Management',
  'q-fin.ST': 'Statistical Finance',
  'q-fin.TR': 'Trading and Market Microstructure',
  // Standalone physics archives
  'gr-qc': 'General Relativity and Quantum Cosmology',
  'hep-ex': 'High Energy Physics - Experiment',
  'hep-lat': 'High Energy Physics - Lattice',
  'hep-ph': 'High Energy Physics - Phenomenology',
  'hep-th': 'High Energy Physics - Theory',
  'math-ph': 'Mathematical Physics',
  'nucl-ex': 'Nuclear Experiment',
  'nucl-th': 'Nuclear Theory',
  'quant-ph': 'Quantum Physics',
};

export function categoryName(code: string): string | undefined {
  return CATEGORY_NAMES[code];
}

/**
 * The individual categories a reader can tick under one subject.
 *
 * A subject defined by whole archives expands to every code in them; one defined by a code list
 * is that list. Sorted by code so the checklist reads the way arXiv writes it. Physical sciences
 * expands to well over a hundred codes and is deliberately left as the subject alone - a
 * checklist that long is a wall, not a control.
 */
export function subjectCategories(key: string): { code: string; name: string }[] {
  const subject = SUBJECTS.find((option) => option.key === key);
  if (!subject || key === 'physical') return [];
  const codes = new Set(subject.categories);
  for (const archive of subject.archives) {
    for (const code of Object.keys(CATEGORY_NAMES)) {
      if (code.startsWith(`${archive}.`)) codes.add(code);
    }
  }
  return [...codes]
    .sort()
    .map((code) => ({ code, name: CATEGORY_NAMES[code] ?? code }));
}
