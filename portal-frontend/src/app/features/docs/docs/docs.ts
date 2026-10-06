import { Component, OnInit, ViewEncapsulation } from '@angular/core';

@Component({
  selector: 'app-docs',
  standalone: true,
  templateUrl: './docs.html',
  styleUrl: './docs.css',
  encapsulation: ViewEncapsulation.Emulated,
})
export class Docs implements OnInit {
  ngOnInit(): void {}
})
