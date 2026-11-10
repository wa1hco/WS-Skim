/* Derived from WSJT-X lib/crc13.cpp (https://github.com/WSJTX/wsjtx).
 * Copyright the WSJT Development Team and the original authors.
 * Licensed under the GNU General Public License, version 3 (GPL-3.0).
 * https://www.gnu.org/licenses/gpl-3.0.txt
 *
 * Checked 2026-11-10 against WSJT-X master: the code matches lib/crc13.cpp
 * aside from newline convention. That upstream file has no per-file
 * copyright line, so no individual author is named here. See NOTICE.
 */

#include <boost/crc.hpp>
#include <boost/config.hpp>

extern "C"
{
   short crc13 (unsigned char const * data, int length);
   bool crc13_check (unsigned char const * data, int length);
}

#define POLY 0x15D7

#ifdef BOOST_NO_CXX11_CONSTEXPR
#define TRUNCATED_POLYNOMIAL POLY
#else
namespace
{
  unsigned long constexpr TRUNCATED_POLYNOMIAL = POLY;
}
#endif

// assumes CRC is last 13 bits of the data and is set to zero
// caller should assign the returned CRC into the message in big endian byte order
short crc13 (unsigned char const * data, int length)
{
    return boost::augmented_crc<13, TRUNCATED_POLYNOMIAL> (data, length);
}

bool crc13_check (unsigned char const * data, int length)
{
   return !boost::augmented_crc<13, TRUNCATED_POLYNOMIAL> (data, length);
}
