import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { Docs } from './docs';
import { HelpCorpus } from '../../../core/services/help-content.service';

const SECTIONS = [
  {
    source_id: 'section:api-keys',
    section_id: 'api-keys',
    document_id: 'getting-started',
    title: 'Get started with AgentShield',
    heading: 'API keys',
    body: 'Create an API key.',
    blocks: [{ type: 'paragraph' as const, text: 'Create an API key.' }],
  },
  {
    source_id: 'section:troubleshooting',
    section_id: 'troubleshooting',
    document_id: 'getting-started',
    title: 'Get started with AgentShield',
    heading: 'Troubleshooting',
    body: 'When something fails.',
    blocks: [{ type: 'paragraph' as const, text: 'When something fails.' }],
  },
];

const CORPUS: HelpCorpus = {
  documents: [
    { document_id: 'getting-started', title: 'Get started with AgentShield', sections: SECTIONS },
  ],
  sections: SECTIONS,
  suggestions: [],
};

describe('Help center', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Docs],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  const render = () => {
    const fixture = TestBed.createComponent(Docs);
    fixture.detectChanges();
    http.expectOne('/help-center-content.json').flush(CORPUS);
    fixture.detectChanges();
    return fixture;
  };

  it('should create', () => {
    expect(render().componentInstance).toBeTruthy();
  });

  it('renders a section navigation whose links point at each section', () => {
    const el = render().nativeElement as HTMLElement;
    const nav = el.querySelector('nav.docs-nav');
    expect(nav).toBeTruthy();
    const links = Array.from(nav!.querySelectorAll('a'));
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['#api-keys', '#troubleshooting']);
    expect(links.map((a) => a.textContent?.trim())).toEqual(['API keys', 'Troubleshooting']);
  });

  it('makes every section heading an anchor link to its own section', () => {
    const el = render().nativeElement as HTMLElement;
    const headingLinks = Array.from(el.querySelectorAll('.docs-section h3 a'));
    expect(headingLinks.length).toBe(2);
    for (const link of headingLinks) {
      const href = link.getAttribute('href') ?? '';
      expect(href.startsWith('#')).toBe(true);
      expect(el.querySelector(href)).toBeTruthy();
    }
  });
});
