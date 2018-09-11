# -*- coding: ISO-8859-1 -*-



'''Quick and dirty PDF writer

Usage example:

import qdpdf
f = open('blah.pdf','wb')
pdf = qdpdf.PDFWriter(f)
pdf.add_page(open('blah.jpg', 'rb'), 800, 600, resolution = 150)
pdf.add_page(open('blah2.jpg', 'rb'), 572, 573, resolution = 96)
pdf.add_page(open('blah3.jpg', 'rb'), 1508, 1376, grayscale = True)
pdf.close()
f.close()
'''



import uuid


#JPEG 'start of image' marker
SOI = '\xFF\xD8'
SOI_size = len(SOI)

#probably a safe minimum length
min_jpeg_len = 120

#how many bytes to copy from image files to the pdf at once
buffer_size = 16384



class PDFWriter(object):
    '''
    Writes JPEG images as pages of a PDF file.
    Images are immediately written to the file, to save memory.
    The images must use 8 bits per channel and may be RGB or grayscale.
    Resulting files contain a random UUID in the trailer ID field.
    Methods of this class may raise:
        IOError (insufficient disk space, etc);
        RuntimeError (closing before adding pages or adding pages after closing);
        ValueError (invalid parameters passed to add_page).
    '''
    
    def __init__(self, file):
        self._file = file
        self.last_obj = 1 #object 1 reserved for page list
        self.page_refs = []
        self.obj_offsets = []
        self.page_count = 0
        self.xref_offset = 0
        self.xref_size = 0
        
        self._save_offset() #for object 0 in xref table
        self.initial_offset = self.obj_offsets[0]
        self.obj_offsets.append(8) #reserved for page list
        
        self._writeln('%PDF-1.2')
        self._writeln('%\xFFççç\0') #indicates document contains binary data
    
    def _check_closed(self):
        if self._file is None:
            raise RuntimeError('PDF closed.')
    
    def _save_offset(self):
        self._check_closed()
        self.obj_offsets.append(self._file.tell())
    
    def _writeln(self, data):
        self._check_closed()
        self._file.write(data)
        self._file.write('\x0A')
    
    def _write_obj(self, ref):
        self._writeln('%s 0 obj' % (ref))
    
    def _write_endobj(self):
        self._writeln('endobj')
    
    def _pixels_to_points(self, pixels, resolution):
        return int(pixels / float(resolution) * 72) #72 postscript points in an inch
    
    def _write_page(self, ref, image_ref, contents_ref, width_pt, height_pt):
        self._save_offset()
        self._write_obj(ref)
        self._writeln('<</Type /Page /Parent 1 0 R /Contents %s 0 R' % (contents_ref))
        self._writeln('/Resources <</ProcSet [ /PDF /ImageC ] /XObject <</image %s 0 R>> >>' % (image_ref))
        self._writeln('/MediaBox [0 0 %s %s]>>' % (width_pt, height_pt))
        self._write_endobj()
    
    def _write_image(self, ref, width_px, height_px, colorspace, jpegdata, data_len, jpeg_file):
        self._save_offset()
        self._write_obj(ref)
        self._writeln('<</Type /XObject /Subtype /Image /Width %s /Height %s /ColorSpace %s' % (width_px, height_px, colorspace))
        self._writeln('/BitsPerComponent 8 /Filter /DCTDecode /Length %s>> stream' % (data_len))
        
        if jpeg_file: #write small chunks to save memory
            for chunk in iter(lambda:jpegdata.read(buffer_size), ''):
                self._file.write(chunk)
            self._writeln('')
        
        else:
            self._writeln(jpegdata)
        
        self._writeln('endstream')
        self._write_endobj()
    
    def _write_contents(self, ref, width_pt, height_pt):
        page_contents = 'q %s 0 0 %s 0 0 cm /image Do Q ' % (width_pt, height_pt)
        
        self._save_offset()
        self._write_obj(ref)
        self._writeln('<</Length %s>> stream' % (len(page_contents)))
        self._writeln(page_contents)
        self._writeln('endstream')
        self._write_endobj()
    
    def _write_catalog(self, ref):
        self._save_offset()
        self._write_obj(ref)
        self._writeln('<</Type /Catalog /Pages 1 0 R>>')
        self._write_endobj()
        self.catalog_ref = ref
    
    def _write_page_list(self):
        self._check_closed()
        
        assert self.page_count >= 1
        assert self.page_count == len(self.page_refs)
        
        page_refs = ''
        for page_ref in self.page_refs:
            page_refs += '%s 0 R ' % (page_ref)
        
        self.obj_offsets[1] = self._file.tell()
        self._write_obj(1)
        self._writeln('<</Type /Pages /Count %s /Kids [%s]>>' % (self.page_count, page_refs))
        self._write_endobj()
    
    def _write_xref(self):
        self._check_closed()
        
        self.xref_offset = self._file.tell()
        self.xref_size = self.last_obj + 1
        
        assert self.xref_size >= 6 #a minimal PDF needs 6 objects
        assert self.xref_size == len(self.obj_offsets)
        
        self._writeln('xref')
        self._writeln('0 %s' % (self.xref_size))
        for offset in self.obj_offsets:
            if offset == self.initial_offset: #object 0
                revision = 65535
                status = 'f' #disabled
            else:
                revision = 0
                status = 'n' #active
            
            self._writeln('%010d %05d %s ' % (offset, revision, status))
    
    def _write_trailer(self, catalog_ref):
        assert 0 not in (self.xref_offset, self.xref_size, catalog_ref)
        
        id = str(uuid.uuid4())
        
        self._writeln('trailer <</Size %s /Root %s 0 R /ID [(%s)(%s)]>>' % (self.xref_size, catalog_ref, id, id))
        self._writeln('startxref %s' % (self.xref_offset))
        self._writeln('%%EOF')
    
    def add_page(self, jpegdata, width_px, height_px, resolution = 72, grayscale = False):
        '''
        Adds a page to the file.
        Page size is determined by image size (pixels) and resolution (dpi).
        If the image is grayscale, use grayscale = True.
        jpegdata may be a string or a readable file-like object.
        If jpegdata is a file-like object, it must support read(), seek() and
        tell(), and must contain only jpeg data.
        '''
        
        try: #is jpegdata a string or a file?
            data_len = len(jpegdata)
        
        except Exception: #a file (different file-like objects cause different exceptions on len())
            jpeg_file = True
            jpegdata.seek(0)
            read_soi = jpegdata.read(SOI_size)
            jpegdata.seek(0, 2) #end of file
            data_len = jpegdata.tell()
            jpegdata.seek(0) #back to the beginning
            
        else: #a string
            jpeg_file = False
            read_soi = jpegdata[:SOI_size]
        
        if read_soi != SOI:
            raise ValueError('Invalid image data.')
        
        if data_len < min_jpeg_len:
            raise ValueError('Invalid image length.')
        
        width_px, height_px = int(width_px), int(height_px)
        resolution, grayscale = int(resolution), bool(grayscale)
        
        if width_px < 1:
            raise ValueError('Invalid image width.')
        
        if height_px < 1:
            raise ValueError('Invalid image height.')
        
        if resolution < 1:
            raise ValueError('Invalid image resolution.')
        
        page_ref = self.last_obj + 1
        image_ref = page_ref + 1
        contents_ref = image_ref + 1
        self.last_obj = contents_ref
        self.page_refs.append(page_ref)
        self.page_count += 1
        
        width_pt = self._pixels_to_points(width_px, resolution)
        height_pt = self._pixels_to_points(height_px, resolution)
        
        if grayscale:
            colorspace = '/DeviceGray'
        else:
            colorspace = '/DeviceRGB'
        
        self._write_page(page_ref, image_ref, contents_ref, width_pt, height_pt)
        self._write_image(image_ref, width_px, height_px, colorspace, jpegdata, data_len, jpeg_file)
        self._write_contents(contents_ref, width_pt, height_pt)
    
    def close(self):
        '''
        Writes final structures to the file.
        This method must be explicitly called to ensure the file is readable.
        '''
        
        if self.page_count < 1:
            raise RuntimeError('No pages added to PDF.')
        
        catalog_ref = self.last_obj + 1
        self.last_obj = catalog_ref
        
        self._write_catalog(catalog_ref)
        self._write_page_list()
        self._write_xref()
        self._write_trailer(catalog_ref)
        
        self._file = None
